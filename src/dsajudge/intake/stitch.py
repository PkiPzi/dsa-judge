"""Combine photos of one question (PLAN.md §7.1, collect/transcribe).

A question often arrives as several photos: pages that overlap by a few lines, or a code listing
that runs over the edge of one photo and carries on in the next. Each photo is transcribed on its
own (one image per model call), so consecutive image sections are stitched afterwards, by text
alone and without the model:

* lines at the top of a photo that repeat the bottom of the previous one are dropped;
* a code block that is still open at the end of a photo and a code block at the start of the next
  photo are joined into one block, so a reference solution split over two photos stays one file.

Only *adjacent* image sources are stitched, in the order the collect stage gave them (file name,
then photo time). Originals and per-photo transcripts are untouched; stitching reruns on every
`--resume`, so it is deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from dsajudge.intake.sources import SourceRec

FENCE_LINE = re.compile(r"^[ \t]*```[ \t]*([\w+#-]*)[ \t]*$")
MAX_OVERLAP = 25
# One repeated line is only trusted as an overlap if it is long enough to be distinctive.
MIN_SINGLE_LINE = 30


@dataclass
class StitchResult:
    sections: dict[int, str]
    notes: list[str] = field(default_factory=list)


def _norm(line: str) -> str:
    return " ".join(line.split())


def _is_fence(line: str) -> bool:
    return FENCE_LINE.match(line) is not None


def _content_index(lines: list[str]) -> list[int]:
    """Positions of lines that carry content (not blank, not a code fence marker)."""
    return [i for i, ln in enumerate(lines) if ln.strip() and not _is_fence(ln)]


def drop_overlap(prev: str, cur: str) -> tuple[str, int]:
    """Remove from the top of ``cur`` the lines that repeat the bottom of ``prev``.

    Returns the new text and how many lines were dropped. Fence markers and blank lines never
    take part in the comparison, so an overlap inside a code block is found as well.
    """
    a, b = prev.split("\n"), cur.split("\n")
    ai, bi = _content_index(a), _content_index(b)
    best = 0
    for k in range(min(MAX_OVERLAP, len(ai), len(bi)), 0, -1):
        tail = [_norm(a[i]) for i in ai[-k:]]
        head = [_norm(b[i]) for i in bi[:k]]
        if tail != head:
            continue
        if k == 1 and len(tail[0]) < MIN_SINGLE_LINE:
            continue
        best = k
        break
    if not best:
        return cur, 0
    drop = set(bi[:best])
    kept = [ln for i, ln in enumerate(b) if i not in drop]
    return "\n".join(kept).strip("\n"), best


def _brace_balance(code: str) -> int:
    code = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', code)
    code = re.sub(r"'(?:\\.|[^'\\\n])*'", "''", code)
    code = re.sub(r"//[^\n]*", "", code)
    return sum(code.count(c) for c in "{([") - sum(code.count(c) for c in "})]")


def block_is_open(lang: str, code: str) -> bool:
    """Does this code block look cut off (unbalanced brackets, or a dangling Python header)?"""
    if _brace_balance(code) > 0:
        return True
    last = next((ln for ln in reversed(code.split("\n")) if ln.strip()), "")
    return lang in ("python", "py", "") and last.rstrip().endswith((":", ",", "\\"))


def _split_last_block(text: str) -> tuple[str, str, str] | None:
    """(before, lang, code) for the last fenced block, if the text ends with it."""
    lines = text.split("\n")
    idx = [i for i, ln in enumerate(lines) if _is_fence(ln)]
    if len(idx) < 2 or len(idx) % 2:
        return None
    o, c = idx[-2], idx[-1]
    if any(ln.strip() for ln in lines[c + 1 :]):
        return None
    m = FENCE_LINE.match(lines[o])
    assert m
    return "\n".join(lines[:o]), m.group(1).lower(), "\n".join(lines[o + 1 : c])


def _split_first_block(text: str) -> tuple[str, str, str] | None:
    """(lang, code, rest) for a fenced block that opens the text (only blank lines before it)."""
    lines = text.split("\n")
    first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if first is None or not _is_fence(lines[first]):
        return None
    close = next((i for i in range(first + 1, len(lines)) if _is_fence(lines[i])), None)
    m = FENCE_LINE.match(lines[first])
    assert m
    if close is None:  # the photo ended inside the block
        return m.group(1).lower(), "\n".join(lines[first + 1 :]), ""
    return m.group(1).lower(), "\n".join(lines[first + 1 : close]), "\n".join(lines[close + 1 :])


CONTINUES = re.compile(r"(?:return|else|elif|except|finally|break|continue)\b")


def _reindent(head: str, prev_code: str) -> str:
    """Indent the lines of ``head`` up to its first top-level def / class like the code above.

    shortcut: one indent for the whole run; a continuation that dedents inside itself needs the
    reviewer.
    """
    lines = prev_code.split("\n")
    last = next((ln for ln in reversed(lines) if ln.strip()), "")
    pad = last[: len(last) - len(last.lstrip())] + ("    " if last.rstrip().endswith(":") else "")
    if head.lstrip().startswith("return"):  # a closing return sits at the function's top level
        fn = next((ln for ln in reversed(lines) if ln.lstrip().startswith("def ")), None)
        if fn is not None:
            pad = fn[: len(fn) - len(fn.lstrip())] + "    "
    out, inside = [], True
    for ln in head.split("\n"):
        inside = inside and not re.match(r"(?:def|class|import|from)\b", ln)
        out.append(pad + ln if inside and ln.strip() else ln)
    return "\n".join(out)


def join_code(prev: str, cur: str) -> tuple[str, str, bool]:
    """Continue ``prev``'s final code block with ``cur``'s first one when ``prev`` looks cut off.

    Returns the new (prev, cur) texts and whether a join happened; the joined block lives in
    ``prev`` and is removed from ``cur``.
    """
    tail = _split_last_block(prev)
    head = _split_first_block(cur)
    if tail is None or head is None:
        return prev, cur, False
    before, lang, code = tail
    head_lang, head_code, rest = head
    if head_lang and lang and head_lang != lang:
        return prev, cur, False
    first_line = next((ln for ln in head_code.split("\n") if ln.strip()), "")
    indented = first_line[:1] in (" ", "\t")
    py = lang in ("python", "py", "")
    # a statement that cannot start a module, with the photo's indentation lost: it continues
    # the function above, so it takes the indentation of the line before it
    dangling = py and not indented and CONTINUES.match(first_line) is not None
    if not (block_is_open(lang, code) or (indented and py) or dangling):
        return prev, cur, False
    if dangling:
        head_code = _reindent(head_code, code)
    joined = f"{code.rstrip(chr(10))}\n{head_code.strip(chr(10))}"
    fence = lang or head_lang
    new_prev = (before + "\n" if before else "") + f"```{fence}\n{joined}\n```"
    return new_prev, rest.strip("\n"), True


def stitch_pages(recs: list[SourceRec], sections: dict[int, str]) -> StitchResult:
    """Stitch every run of adjacent image sources; other sources pass through unchanged."""
    out = dict(sections)
    notes: list[str] = []
    prev_rec: SourceRec | None = None
    for rec in recs:
        if prev_rec is not None and prev_rec.kind == "image" and rec.kind == "image":
            a, b = out[prev_rec.n], out[rec.n]
            b, dropped = drop_overlap(a, b)
            if dropped:
                notes.append(
                    f"photos {prev_rec.original} and {rec.original} overlap: "
                    f"{dropped} repeated line(s) dropped from the second"
                )
            a, b, joined = join_code(a, b)
            if joined:
                notes.append(
                    f"code continues from {prev_rec.original} onto {rec.original}: "
                    "joined into one block"
                )
            out[prev_rec.n], out[rec.n] = a, b
        prev_rec = rec
    return StitchResult(out, notes)
