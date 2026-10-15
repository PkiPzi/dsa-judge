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

from dsajudge.ai.base import LLM
from dsajudge.ai.retry import ask_structured
from dsajudge.ai.schemas import BundleScan

OPEN_HEAD_LINES = 4  # statement lines of the open question shown to the model
OPEN_TAIL_LINES = 3  # latest lines shown, so it can tell a finished question from a running one
MIN_PROSE_CHARS = 40  # a group with less statement text than this is a fragment
SAME_QUESTION = 0.85  # head similarity above which two groups are the same question
HEAD_CHARS = 160  # how much statement text is compared
MIN_HEAD = 25  # shorter statements are never merged as repeats
MIN_REPEAT_LINE = 30  # a prose line this long is dropped when it repeats inside a group
FENCE = re.compile(r"^[ \t]*```")


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


def _scan_page(
    llm: LLM, page: str, lines: list[str], groups: list[Group], sections: dict[int, str], tries: int
) -> dict[int, str]:
    """Line number -> title of each question the model says begins on this page."""
    count = len(lines)

    def check(scan: BundleScan) -> str | None:
        bad = [s.line for s in scan.starts if not 1 <= s.line <= count]
        return f"line {bad[0]} does not exist: this page has lines 1 to {count}" if bad else None

    scan = ask_structured(
        llm,
        "split_bundle",
        BundleScan,
        {"open": _open_summary(groups, sections), "page": page},
        attempts=tries,
        check=check,
    )
    return {s.line: s.title.strip() for s in reversed(scan.starts)}


def split_bundle(llm: LLM, sections: dict[int, str], attempts: int = 3) -> list[Group]:
    """The questions of a bundle, as ordered groups of line ranges of the pages."""
    groups: list[Group] = []
    for n in sorted(sections):
        lines = sections[n].split("\n")
        if not any(ln.strip() for ln in lines):
            continue
        inside = _inside_code(lines)
        prose = [i + 1 for i, ln in enumerate(lines) if ln.strip() and not inside[i]]
        starts: dict[int, str] = {}
        if prose:
            page = "\n".join(f"[{i}] {ln}" for i, ln in enumerate(lines, 1))
            found = _scan_page(llm, page, lines, groups, sections, attempts)
            # a boundary is only believed on a statement line, never inside a code block
            starts = {line: title for line, title in found.items() if line in prose}
        if not groups:
            starts.setdefault(prose[0] if prose else 1, "")
        bounds = sorted(starts)
        first = bounds[0] if bounds else len(lines) + 1
        if groups and first > 1:
            groups[-1].spans.append(Span(section=n, start=1, end=first - 1))
        for i, b in enumerate(bounds):
            end = bounds[i + 1] - 1 if i + 1 < len(bounds) else len(lines)
            groups.append(Group(title=starts[b], spans=[Span(section=n, start=b, end=end)]))
    groups = _merge_fragments(groups, sections)
    groups = _merge_repeats(groups, sections)
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


def _head(group: Group, sections: dict[int, str]) -> str:
    """The first HEAD_CHARS of the group's statement text, case and spacing normalised."""
    return " ".join(" ".join(_prose(group, sections)).lower().split())[:HEAD_CHARS]


def _same_question(a: str, b: str) -> bool:
    # equal-length prefixes: a restated question may stop earlier or run on with other text
    n = min(len(a), len(b))
    return n >= MIN_HEAD and difflib.SequenceMatcher(None, a[:n], b[:n]).ratio() >= SAME_QUESTION


def _merge_repeats(groups: list[Group], sections: dict[int, str]) -> list[Group]:
    """A question photographed twice (or restated later) is one question."""
    out: list[Group] = []
    heads: list[str] = []
    for g in groups:
        head = _head(g, sections)
        for i, seen in enumerate(heads):
            if _same_question(seen, head):
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
