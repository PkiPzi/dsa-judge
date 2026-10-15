"""Bundle intake: page order, and splitting a photo bundle into questions (scripted model)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dsajudge.intake.bundle import group_pages, split_bundle
from dsajudge.intake.sources import order_sources


def test_copy_suffixed_names_keep_arrival_order(tmp_path: Path) -> None:
    names = [
        "WhatsApp Image at 3.44.42 AM (2).jpeg",
        "WhatsApp Image at 3.44.41 AM (1).jpeg",
        "WhatsApp Image at 3.44.42 AM.jpeg",
        "WhatsApp Image at 3.44.41 AM.jpeg",
        "WhatsApp Image at 3.44.42 AM (1).jpeg",
        "p (10).png",
        "p (2).png",
        "p.png",
    ]
    for n in names:
        (tmp_path / n).write_bytes(b"x")
    got = [p.name for p in order_sources([tmp_path / n for n in names])]
    assert got[:3] == ["p.png", "p (2).png", "p (10).png"]
    assert got[3:] == [
        "WhatsApp Image at 3.44.41 AM.jpeg",
        "WhatsApp Image at 3.44.41 AM (1).jpeg",
        "WhatsApp Image at 3.44.42 AM.jpeg",
        "WhatsApp Image at 3.44.42 AM (1).jpeg",
        "WhatsApp Image at 3.44.42 AM (2).jpeg",
    ]


class Scripted:
    """Answers split_bundle with the lines starting `Problem`, plus any extra `claims`."""

    def __init__(self, claims: dict[str, list[int]] | None = None) -> None:
        self.claims = claims or {}
        self.calls = 0

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict[str, object] | None = None,
        images: list[Path] | None = None,
    ) -> str:
        self.calls += 1
        page = prompt.split("This page, with line numbers:\n", 1)[1]
        starts = []
        for m in re.finditer(r"^\[(\d+)\] Problem (\w+)", page, flags=re.MULTILINE):
            starts.append({"line": int(m.group(1)), "title": f"Problem {m.group(2)}"})
        for key, lines in self.claims.items():
            if key in page:
                starts += [{"line": n, "title": "claimed"} for n in lines]
        return json.dumps({"starts": starts})


STATEMENT_A = "Problem A: add two numbers\nGiven two integers a and b, return their sum.\n"
STATEMENT_B = "Problem B: reverse a string\nGiven a string s, return it reversed.\n"
CODE_A = "```cpp\nint add(int a, int b) { return a + b; }\n```"
CODE_B = "```python\ndef rev(s):\n    return s[::-1]\n```"


def test_questions_are_split_inside_and_across_pages() -> None:
    sections = {
        1: STATEMENT_A + CODE_A,
        # a page that ends question A and starts B
        2: "Time complexity is constant, nothing else to say here.\n" + STATEMENT_B,
        3: CODE_B,
        # the first question photographed again
        4: STATEMENT_A,
    }
    groups = split_bundle(Scripted(), sections)
    assert [g.title for g in groups] == ["Problem A", "Problem B"]
    a, b = (group_pages(g, sections) for g in groups)
    assert [n for n, _ in a][:2] == [1, 2]  # page 2's first line belongs to A
    assert "Time complexity" in a[1][1] and "Problem B" not in a[1][1]
    assert [n for n, _ in b] == [2, 3]
    assert "def rev" in b[1][1]
    # the restated statement on page 4 is merged into A, its repeated long line dropped
    assert [n for n, _ in a] == [1, 2, 4]
    assert a[-1][1].count("Given two integers") == 0


def test_a_boundary_inside_a_code_block_is_ignored() -> None:
    sections = {1: STATEMENT_A + CODE_A, 2: "```cpp\nint a;\nint b;\n```\n" + STATEMENT_B}
    # the model wrongly claims line 3 of page 2 (inside the fence) starts a question
    groups = split_bundle(Scripted({"int a;": [3]}), sections)
    assert [g.title for g in groups] == ["Problem A", "Problem B"]


def test_a_fragment_joins_its_neighbour() -> None:
    sections = {1: STATEMENT_A + CODE_A + "\nSolution:\n", 2: STATEMENT_B}
    # "Solution:" is claimed as a new question but has no statement text
    groups = split_bundle(Scripted({"Solution:": [6]}), sections)
    assert [g.title for g in groups] == ["Problem A", "Problem B"]


def test_a_single_question_stays_one_group_and_untitled_ones_are_numbered() -> None:
    groups = split_bundle(Scripted(), {1: "Count the islands in a grid of 0s and 1s.\n" + CODE_A})
    assert len(groups) == 1 and groups[0].title == "question 1"


def test_bad_line_numbers_are_retried_then_reported() -> None:
    class Wrong(Scripted):
        def complete(self, prompt: str, **kw: object) -> str:  # type: ignore[override]
            self.calls += 1
            return json.dumps({"starts": [{"line": 99, "title": "x"}]})

    from dsajudge.ai.base import AIError

    llm = Wrong()
    with pytest.raises(AIError, match="does not exist"):
        split_bundle(llm, {1: STATEMENT_A}, attempts=2)
    assert llm.calls == 2
