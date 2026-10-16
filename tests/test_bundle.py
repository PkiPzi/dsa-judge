"""Bundle intake: page order, and splitting a photo bundle into questions (scripted model)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dsajudge.intake.bundle import drop_repeated_runs, group_pages, split_bundle
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
    """Answers split_bundle with each page line starting `Problem`, plus any extra `claims`
    (a substring of the page -> lines the model wrongly says start a question)."""

    def __init__(self, claims: dict[str, list[str]] | None = None) -> None:
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
        page = prompt.split("This page:\n", 1)[1]
        starts = [
            {"first_line": m.group(0), "title": f"Problem {m.group(1)}"}
            for m in re.finditer(r"^Problem (\w+).*$", page, flags=re.MULTILINE)
        ]
        for key, quotes in self.claims.items():
            if key in page:
                starts += [{"first_line": q, "title": "claimed"} for q in quotes]
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
    groups = split_bundle(Scripted({"int a;": ["int a;"]}), sections)
    assert [g.title for g in groups] == ["Problem A", "Problem B"]


def test_a_fragment_joins_its_neighbour() -> None:
    sections = {1: STATEMENT_A + CODE_A + "\nSolution:\n", 2: STATEMENT_B}
    # "Solution:" is claimed as a new question but has no statement text
    groups = split_bundle(Scripted({"Solution:": ["Solution:"]}), sections)
    assert [g.title for g in groups] == ["Problem A", "Problem B"]


def test_a_single_question_stays_one_group_and_untitled_ones_are_numbered() -> None:
    groups = split_bundle(Scripted(), {1: "Count the islands in a grid of 0s and 1s.\n" + CODE_A})
    assert len(groups) == 1 and groups[0].title == "question 1"


def test_a_model_that_only_misquotes_is_ignored_not_fatal() -> None:
    class Wrong(Scripted):
        def complete(self, prompt: str, **kw: object) -> str:  # type: ignore[override]
            self.calls += 1
            return json.dumps({"starts": [{"first_line": "no such line anywhere", "title": "x"}]})

    llm = Wrong()
    groups = split_bundle(llm, {1: STATEMENT_A, 2: CODE_A}, attempts=2)
    assert llm.calls == 2  # the one prose page, twice; the code-only page needs no model
    assert len(groups) == 1


def test_a_statement_after_code_is_a_new_question_even_if_the_model_misses_it() -> None:
    class Blind(Scripted):
        def complete(self, prompt: str, **kw: object) -> str:  # type: ignore[override]
            self.calls += 1
            return json.dumps({"starts": []})

    cued = (
        "Count the islands\nGiven a grid of 0s and 1s, count the islands.\n"
        "Constraints:\nExample 1: Input: grid = []"
    )
    groups = split_bundle(Blind(), {1: STATEMENT_A + "Constraints:\n" + CODE_A, 2: cued})
    assert len(groups) == 2


def test_false_followups_are_merged_but_real_ones_survive() -> None:
    from dsajudge.ai.schemas import CodeBlockRef, Segmentation, SegmentedPart
    from dsajudge.intake.structure import merge_false_followups

    code = [
        CodeBlockRef(section=2, lang="cpp", part=1),
        CodeBlockRef(section=3, lang="cpp", part=2),  # same part/lang/role once merged: dropped
        CodeBlockRef(section=4, lang="cpp", part=3),
    ]
    seg = Segmentation(
        title="t",
        parts=[
            SegmentedPart(part=1, text="Given an array, return the sum."),
            SegmentedPart(part=2, text="Constraints:\n1 <= n <= 5\nExample 1: ..."),
            SegmentedPart(part=3, text="Follow-up: can you do it in O(1) space?"),
        ],
        code=code,
    )
    out = merge_false_followups(seg)
    assert [p.part for p in out.parts] == [1, 2]
    assert "Constraints:" in out.parts[0].text and out.parts[1].text.startswith("Follow-up")
    assert [(c.section, c.part) for c in out.code] == [(2, 1), (4, 2)]


def test_empty_code_blocks_left_by_overlap_trimming_are_removed() -> None:
    sections = {1: "Count the islands in the grid.\n```cpp\n```\nConstraints: n <= 5\n"}
    assert "```" not in drop_repeated_runs(sections)[1]


class _Rung:
    """A vision stand-in whose sampling level rises with each with_options() rung."""

    def __init__(self, level: int = 0) -> None:
        self.level = level

    def with_options(self, **options: object) -> _Rung:
        return _Rung(1 if options.get("temperature") == 0.3 else 2)


def _robust(
    monkeypatch: object, needs_level: int, error: str = "token repeat limit"
) -> tuple[str, list[str]]:
    from typing import Any, cast

    from dsajudge.ai.base import AIError
    from dsajudge.config import Config
    from dsajudge.intake import pipeline
    from dsajudge.intake.sources import SourceRec

    def fake(rec: Any, raw: Any, work: Any, vision: _Rung, audio: Any, side: int) -> str:
        if vision.level < needs_level:
            raise AIError(error)
        return f"text@{vision.level}"

    def halves(rec: Any, raw: Any, work: Any, vision: _Rung, side: int) -> str:
        if vision.level < needs_level:
            raise AIError(error)
        return f"halves@{vision.level}"

    cast(pytest.MonkeyPatch, monkeypatch).setattr(pipeline, "transcribe_one", fake)
    cast(pytest.MonkeyPatch, monkeypatch).setattr(pipeline, "transcribe_halves", halves)
    env = pipeline.Env(cfg=Config(), providers=None, backend=None)  # type: ignore[arg-type]
    state = pipeline.State(key="k", mode="bundle")
    rec = SourceRec(n=1, name="1.jpg", original="1.jpg", kind="image", sha256="x")
    out = pipeline._transcribe_robust(env, state, rec, Path("."), Path("."), _Rung(), None)  # type: ignore[arg-type]
    return out, state.notes


def test_a_looping_photo_is_retried_with_other_sampling(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _robust(monkeypatch, needs_level=1) == ("text@1", [])
    assert _robust(monkeypatch, needs_level=2) == ("text@2", [])


def test_an_unreadable_photo_becomes_an_unreadable_page(monkeypatch: pytest.MonkeyPatch) -> None:
    out, notes = _robust(monkeypatch, needs_level=9)
    assert out.startswith("[??]") and "marked [??]" in notes[0]


def test_halves_are_tried_after_the_whole_photo_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from typing import Any, cast

    from dsajudge.ai.base import AIError
    from dsajudge.config import Config
    from dsajudge.intake import pipeline
    from dsajudge.intake.sources import SourceRec

    def whole(*a: Any) -> str:
        raise AIError("token repeat limit")

    def halves(rec: Any, raw: Any, work: Any, vision: _Rung, side: int) -> str:
        return "joined halves"

    mp = cast(pytest.MonkeyPatch, monkeypatch)
    mp.setattr(pipeline, "transcribe_one", whole)
    mp.setattr(pipeline, "transcribe_halves", halves)
    env = pipeline.Env(cfg=Config(), providers=None, backend=None)  # type: ignore[arg-type]
    state = pipeline.State(key="k", mode="bundle")
    rec = SourceRec(n=1, name="1.jpg", original="1.jpg", kind="image", sha256="x")
    got = pipeline._transcribe_robust(env, state, rec, Path("."), Path("."), _Rung(), None)  # type: ignore[arg-type]
    assert got == "joined halves" and state.notes == []


def test_ollama_being_down_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    from dsajudge.ai.base import AIError

    with pytest.raises(AIError, match="not reachable"):
        _robust(monkeypatch, needs_level=9, error="Ollama is not reachable at x")


def test_image_halves_overlap_and_cover_the_photo(tmp_path: Path) -> None:
    from PIL import Image

    from dsajudge.intake.transcribe import image_halves

    png = tmp_path / "p.png"
    Image.new("RGB", (100, 200), "white").save(png)
    top, bottom = image_halves(png, overlap=0.1)
    with Image.open(top) as t, Image.open(bottom) as b:
        assert (t.width, b.width) == (100, 100)
        assert t.height + b.height == 200 + 20  # the 10% overlap is counted twice
