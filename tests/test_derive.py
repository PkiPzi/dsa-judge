# ruff: noqa: E501
"""A question with no constraints and no examples: the optimized solution supplies both, and the
explanation becomes the editorial. Synthetic problem; no real question text."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from dsajudge.ai import factory
from dsajudge.ai.fake import FakeLLM, FakeTranscriber
from dsajudge.ai.synth import render_text_image
from dsajudge.cli import app
from dsajudge.gen.manifest import load_manifest
from dsajudge.intake.codefence import comment_prose
from dsajudge.model.loader import load_problem
from dsajudge.store.bank import problems_dir

runner = CliRunner()
CODE = (
    "class Solution:\n    def smallestGap(self, nums):\n"
    "        Sort then compare the neighbours\n"
    "        nums = sorted(nums)\n        return min(b - a for a, b in zip(nums, nums[1:]))\n"
)
EXPLANATION = "Sort the array; the answer is the smallest difference of neighbours. O(n log n)."


def test_constraints_samples_and_editorial_come_from_the_solution(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = tmp_path / "in"
    d.mkdir()
    stmt = render_text_image(
        ["Smallest gap", "Return the smallest gap between two values."], d / "1-stmt.png"
    )
    code = render_text_image(["code"], d / "2-code.png")
    llm = FakeLLM(
        [
            {"task": "transcribe_image", "image": "1-stmt.png", "response": "Smallest gap\n\nReturn the smallest gap between two values.\n" + EXPLANATION},
            {"task": "transcribe_image", "image": "2-code.png", "response": "```python\n" + CODE + "```"},
            {"task": "segment", "response": {
                "title": "Smallest gap",
                "parts": [{"part": 1, "text": "Return the smallest gap between two values."}],
                "code": [{"section": 2, "block": 1, "lang": "python", "role": "ref", "part": 1}],
            }},
            {"task": "extract_part", "response": {
                "title": "Smallest gap",
                "statement": "Return the smallest gap between two values of `nums`.",
                "method": "smallestGap", "params": [{"name": "nums", "type": "int[]"}], "returns": "int",
                "editorial": EXPLANATION,
            }},
            {"task": "constraints_from_solution", "response": {"constraints": {"nums": {"len": [2, 50], "each": [-100, 100]}}}},
        ]
    )  # fmt: skip
    monkeypatch.setattr(factory, "PROVIDERS", factory.FixedProviders(llm, FakeTranscriber()))
    monkeypatch.setenv("EDITOR", "true")
    r = runner.invoke(app, ["add", str(stmt), str(code), "--slug", "gap"])
    assert r.exit_code == 0, r.output
    bank = problems_dir() / "gap"
    part = load_problem(bank).part(1)
    assert 1 <= len(part.samples) <= 3
    for s in part.samples:  # the expected value is what the solution returns
        assert s.expected == min(
            b - a for a, b in zip(sorted(s.args[0]), sorted(s.args[0])[1:], strict=False)
        )
    assert "# Sort then compare the neighbours" in (bank / part.refs[0].file).read_text()
    assert (bank / part.editorial).read_text().strip() == EXPLANATION
    assert load_manifest(bank, 1)
    out = runner.invoke(app, ["editorial", "gap"]).output
    assert "smallest difference of neighbours" in out


def test_comment_prose_only_touches_sentences_the_compiler_rejects() -> None:
    cpp = "class Solution {\npublic:\n    int f(vector<int>& a) {\n        Time complexity is O(n)\n        return a[0];\n    }\n};\n"
    assert "// Time complexity is O(n)" in comment_prose(cpp, "cpp")
    broken = "def f(:\n    pass\n"
    assert comment_prose(broken, "python") == broken  # a real syntax error is not prose
    ok = "def f(x):\n    return x\n"
    assert comment_prose(ok, "python") == ok


def test_unlinked_code_is_attached_to_a_single_part() -> None:
    from dsajudge.ai.schemas import Segmentation, SegmentedPart
    from dsajudge.intake.structure import attach_unlinked_code

    seg = Segmentation(title="t", parts=[SegmentedPart(part=1, text="x")])
    sections = {
        1: "text",
        2: "```python\ndef f():\n    pass\n```\n```\nint main() { return 0; }\n```",
    }
    got = attach_unlinked_code(seg, sections)
    assert [(c.section, c.block, c.lang) for c in got.code] == [(2, 1, "python"), (2, 2, "cpp")]
    two = Segmentation(
        title="t", parts=[SegmentedPart(part=1, text="x"), SegmentedPart(part=2, text="y")]
    )
    assert attach_unlinked_code(two, sections).code == []
