"""Bundle intake (PLAN.md §7.1): one set of photos that holds several different questions.

The photos are transcribed like any other set. A narrow model pass then reads the pages in order
and reports where a NEW question begins on each page. A small model is not trusted on its own:
code guards keep it from splitting one question in two (no boundary inside a code block, a group
with almost no statement text is a fragment of its neighbour, a question that reappears is merged)
and a repeated prose line is dropped. Each group then goes through the normal pipeline alone.
"""

from __future__ import annotations

import difflib
import re

from pydantic import BaseModel, Field

from dsajudge.ai.base import LLM, AIError
from dsajudge.ai.retry import ask_structured
from dsajudge.ai.schemas import BundleScan

OPEN_HEAD_LINES = 4  # statement lines of the open question shown to the model
OPEN_TAIL_LINES = 3  # latest lines shown, so it can tell a finished question from a running one
MIN_PROSE_CHARS = 40  # a group with less statement text than this is a fragment
SAME_QUESTION = 0.9  # opening-line similarity above which two groups are the same question
MIN_HEAD = 30  # shorter opening lines (headings, titles) are never merged as repeats
REPEAT_WINDOW = 4  # a restated question shows up within this many photos of the original
MIN_REPEAT_LINE = 30  # a prose line this long is dropped when it repeats inside a group
SHINGLE = 3  # consecutive lines that must repeat for a run to count as a duplicate
MIN_LONG_LINE = 20  # a repeated run needs at least two lines this long
QUOTE_MATCH = 0.75  # how closely the quoted first line must match a line of the page
TITLE_MAX = 70  # a title line is at most this long
FENCE = re.compile(r"^[ \t]*```")
# a code block with nothing in it (what is left when a page's code was all overlap)
EMPTY_FENCE = re.compile(
    r"^[ \t]*```[\w+#-]*[ \t]*\n(?:[ \t]*\n)*[ \t]*```[ \t]*$\n?", re.MULTILINE
)
CUE = re.compile(r"^\s*(example\s*\d*|constraints?|input\s*:|output\s*:|sample)\b", re.IGNORECASE)
# openings that continue a question instead of starting one
NOT_A_STATEMENT = re.compile(
    r"^(time|space|approach|explanation|complexity|idea|solution|note|follow|what if|can you"
    r"|now|also|extend|modify|if )",
    re.IGNORECASE,
)


class Span(BaseModel):
    section: int
    start: int  # 1-based line, inclusive
    end: int


class Group(BaseModel):
    title: str = ""
    spans: list[Span] = Field(default_factory=list)


def _inside_code(lines: list[str]) -> list[bool]:
    """Per line: is it a fence marker or between two of them."""
    out: list[bool] = []
    opened = False
    for ln in lines:
        if FENCE.match(ln):
            out.append(True)
            opened = not opened
        else:
            out.append(opened)
    return out


def _prose(group: Group, sections: dict[int, str]) -> list[str]:
    """The group's non-empty lines outside code blocks, in reading order."""
    out: list[str] = []
    for sp in sorted(group.spans, key=lambda s: (s.section, s.start)):
        lines = sections[sp.section].split("\n")
        inside = _inside_code(lines)
        out += [lines[i] for i in range(sp.start - 1, sp.end) if lines[i].strip() and not inside[i]]
    return out


def _open_summary(groups: list[Group], sections: dict[int, str]) -> str:
    if not groups:
        return "none yet: this is the first page, so its first line starts a question."
    g = groups[-1]
    head = [ln.strip()[:200] for ln in _prose(g, sections)[:OPEN_HEAD_LINES]]
    last = g.spans[-1]
    tail = [
        ln.strip()[:200] for ln in sections[last.section].split("\n")[last.start - 1 : last.end]
    ]
    tail = [ln for ln in tail if ln][-OPEN_TAIL_LINES:]
    return "Begins:\n" + "\n".join(head) + "\nLatest lines:\n" + "\n".join(tail)


def _norm(line: str) -> str:
    return " ".join(line.lower().split())


def _locate(lines: list[str], candidates: list[int], quote: str) -> int | None:
    """0-based index of the candidate line that best matches ``quote``, if it matches well."""
    q = _norm(quote)
    best, found = 0.0, None
    for i in candidates:
        r = difflib.SequenceMatcher(None, q, _norm(lines[i])).ratio()
        if r > best:
            best, found = r, i
    return found if best >= QUOTE_MATCH else None


def _snap_to_title(lines: list[str], prose: list[int], i: int) -> int:
    """If the line above ``i`` is a short title (the model quoted the sentence under it), start
    there. A title is short, unpunctuated and neither a cue ("Example 1") nor an explanation."""
    if i == 0 or (i - 1) not in prose:
        return i
    above = lines[i - 1].strip()
    is_title = (
        len(above) <= TITLE_MAX
        and above[-1] not in ".:;,?!)}"
        and not CUE.match(above)
        and not NOT_A_STATEMENT.match(above)
        and len(lines[i].strip()) > len(above)
    )
    return i - 1 if is_title else i


def _has_code(group: Group, sections: dict[int, str]) -> bool:
    for sp in group.spans:
        inside = _inside_code(sections[sp.section].split("\n"))
        if any(inside[sp.start - 1 : sp.end]):
            return True
    return False


def _cue_starts(
    lines: list[str], prose: list[int], inside: list[bool], has_code: bool
) -> list[int]:
    """Statement blocks (prose with an Example/Constraints/Input cue) that follow code: a second
    opinion that does not depend on the model. 0-based indices of their first lines."""
    out: list[int] = []
    seen_code = has_code
    block: list[int] = []

    def close() -> None:
        if seen_code and block:
            head = lines[block[0]].strip()
            cue = any(CUE.match(lines[i]) for i in block[:14])
            if cue and not CUE.match(head) and not NOT_A_STATEMENT.match(head):
                out.append(block[0])

    for i in range(len(lines)):
        if inside[i]:
            close()
            block = []
            seen_code = True
        elif lines[i].strip():
            block.append(i)
    close()
    return [_snap_to_title(lines, prose, i) for i in out]


def _scan_page(
    llm: LLM,
    lines: list[str],
    prose: list[int],
    groups: list[Group],
    sections: dict[int, str],
    tries: int,
) -> dict[int, str]:
    """0-based line -> title of each question the model says begins on this page."""

    def check(scan: BundleScan) -> str | None:
        # one misquoted line is ignored below; only a reply with no usable line is retried
        if scan.starts and all(_locate(lines, prose, s.first_line) is None for s in scan.starts):
            quote = scan.starts[0].first_line[:80]
            return f"first_line {quote!r} is not a line of this page; copy it exactly"
        return None

    try:
        scan = ask_structured(
            llm,
            "split_bundle",
            BundleScan,
            {"open": _open_summary(groups, sections), "page": "\n".join(lines)},
            attempts=tries,
            check=check,
        )
    except AIError:
        return {}  # the cue rule and the guards still apply to this page
    found: dict[int, str] = {}
    for s in scan.starts:
        at = _locate(lines, prose, s.first_line)
        # a question never opens with "Example 2:" or "Constraints:": that page continues one
        if (
            at is not None
            and not CUE.match(lines[at])
            and not NOT_A_STATEMENT.match(lines[at].strip())
        ):
            found.setdefault(_snap_to_title(lines, prose, at), s.title.strip())
    return found


def drop_repeated_runs(sections: dict[int, str]) -> dict[int, str]:
    """Remove from each page the runs of lines that already appeared on an earlier page.

    Photos of a bundle are often near-copies (a burst, the same page twice); the stitcher only
    handles the end of one photo repeating at the top of the next. A run counts when SHINGLE
    consecutive lines repeat and at least two of them are long enough to be distinctive, so the
    braces and short code lines that really do repeat between questions are never dropped.
    """

    def key(line: str) -> str:
        return " ".join(line.split()).lower()

    def shingles(lines: list[str]) -> list[tuple[str, ...]]:
        ks = [key(ln) for ln in lines]
        return [tuple(ks[i : i + SHINGLE]) for i in range(len(ks) - SHINGLE + 1)]

    def distinctive(sh: tuple[str, ...]) -> bool:
        return sum(len(k) >= MIN_LONG_LINE for k in sh) >= 2

    seen: set[tuple[str, ...]] = set()
    out: dict[int, str] = {}
    for n in sorted(sections):
        lines = sections[n].split("\n")
        content = [i for i, ln in enumerate(lines) if ln.strip() and not FENCE.match(ln)]
        flat = [lines[i] for i in content]
        drop: set[int] = set()
        for j, sh in enumerate(shingles(flat)):
            if sh in seen and distinctive(sh):
                drop.update(content[j : j + SHINGLE])
        seen.update(shingles(flat))
        kept = [ln for i, ln in enumerate(lines) if i not in drop]
        out[n] = EMPTY_FENCE.sub("", "\n".join(kept)).strip("\n")
    return out


def split_bundle(llm: LLM, sections: dict[int, str], attempts: int = 3) -> list[Group]:
    """The questions of a bundle, as ordered groups of line ranges of the pages."""
    groups: list[Group] = []
    for n in sorted(sections):
        lines = sections[n].split("\n")
        if not any(ln.strip() for ln in lines):
            continue
        inside = _inside_code(lines)
        prose = [i for i, ln in enumerate(lines) if ln.strip() and not inside[i]]
        starts: dict[int, str] = {}
        if prose:
            starts = _scan_page(llm, lines, prose, groups, sections, attempts)
            open_code = bool(groups) and _has_code(groups[-1], sections)
            for i in _cue_starts(lines, prose, inside, open_code):
                starts.setdefault(i, "")
        if not groups:
            starts.setdefault(prose[0] if prose else 0, "")
        bounds = sorted(starts)
        first = bounds[0] if bounds else len(lines)
        if groups and first > 0:
            groups[-1].spans.append(Span(section=n, start=1, end=first))
        for i, b0 in enumerate(bounds):
            end = bounds[i + 1] if i + 1 < len(bounds) else len(lines)
            groups.append(Group(title=starts[b0], spans=[Span(section=n, start=b0 + 1, end=end)]))
    groups = _merge_fragments(groups, sections)
    groups = _merge_repeats(groups, sections)
    groups = _merge_continuations(groups, sections)
    groups = _merge_codeless_forward(groups, sections)
    for i, g in enumerate(groups, 1):
        g.title = g.title or f"question {i}"
    return groups


def _is_fragment(group: Group, sections: dict[int, str]) -> bool:
    return sum(len(ln.strip()) for ln in _prose(group, sections)) < MIN_PROSE_CHARS


def _merge_fragments(groups: list[Group], sections: dict[int, str]) -> list[Group]:
    """A group with almost no statement text is the tail (or head) of its neighbour."""
    out: list[Group] = []
    for g in groups:
        if out and _is_fragment(g, sections):
            out[-1].spans += g.spans
        else:
            out.append(g)
    if len(out) > 1 and _is_fragment(out[0], sections):
        out[1].spans = out[0].spans + out[1].spans
        out.pop(0)
    return out


def _merge_codeless_forward(groups: list[Group], sections: dict[int, str]) -> list[Group]:
    """Every question comes with its solution, so a group with no code is a statement whose
    solution is in the next group: join it to that one (the statement's title wins)."""
    out: list[Group] = []
    carry: Group | None = None
    for i, g in enumerate(groups):
        if carry:
            g = Group(title=carry.title or g.title, spans=carry.spans + g.spans)
            carry = None
        if not _has_code(g, sections) and i < len(groups) - 1:
            carry = g
        else:
            out.append(g)
    return out


CONTINUATION = re.compile(
    r"^\W*(follow[- ]?up|look out|discuss|answer|hints?|notes?|helper|definition|approach"
    r"|explanation|edge cases?|test cases?|examples?|solution|complexity|optimi[sz]ation)\b",
    re.IGNORECASE,
)


def _merge_continuations(groups: list[Group], sections: dict[int, str]) -> list[Group]:
    """A section of a question (its follow-up, the aspects to look out for, an answer, a
    helper's definition) is headed like one, but it belongs to the question before it."""
    out: list[Group] = []
    for g in groups:
        prose = _prose(g, sections)
        heading = bool(CONTINUATION.match(g.title)) or bool(
            prose and CONTINUATION.match(prose[0].strip())
        )
        if out and heading:
            out[-1].spans += g.spans
        else:
            out.append(g)
    return out


def _openings(group: Group, sections: dict[int, str]) -> list[str]:
    """The first lines of the group's statement (a title and/or the task sentence), normalised."""
    lines = [ln for ln in _prose(group, sections) if not CUE.match(ln)]
    return [" ".join(ln.lower().split()) for ln in lines[:2]]


def _same_question(a: list[str], b: list[str]) -> bool:
    """A restated question repeats its opening line; two questions that merely share boilerplate
    (the same constraints, "Given an integer array ...") do not, so only openings are compared."""
    return any(
        min(len(x), len(y)) >= MIN_HEAD
        and difflib.SequenceMatcher(None, x, y).ratio() >= SAME_QUESTION
        for x in a
        for y in b
    )


def _merge_repeats(groups: list[Group], sections: dict[int, str]) -> list[Group]:
    """A question photographed twice (or restated a page later) is one question."""
    out: list[Group] = []
    heads: list[list[str]] = []
    for g in groups:
        head = _openings(g, sections)
        first = min(sp.section for sp in g.spans)
        for i, seen in enumerate(heads):
            near = first - max(sp.section for sp in out[i].spans) <= REPEAT_WINDOW
            if near and _same_question(seen, head):
                out[i].spans += g.spans
                break
        else:
            out.append(g)
            heads.append(head)
    return out


def group_pages(group: Group, sections: dict[int, str]) -> list[tuple[int, str]]:
    """(page number, text) of the group in reading order; a long prose line already seen in an
    earlier page of the same group is dropped (code is never touched: its lines repeat for real)."""
    seen: set[str] = set()
    out: dict[int, list[str]] = {}
    for sp in sorted(group.spans, key=lambda s: (s.section, s.start)):
        lines = sections[sp.section].split("\n")
        inside = _inside_code(lines)
        keep: list[str] = []
        for i in range(sp.start - 1, sp.end):
            key = " ".join(lines[i].split()).lower()
            if not inside[i] and len(key) >= MIN_REPEAT_LINE:
                if key in seen:
                    continue
                seen.add(key)
            keep.append(lines[i])
        out.setdefault(sp.section, []).extend(keep)
    return [(n, "\n".join(ls).strip("\n")) for n, ls in sorted(out.items())]
