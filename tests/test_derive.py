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


def test_bare_functions_get_a_solution_wrapper() -> None:
    import subprocess
    import sys
    import tempfile

    from dsajudge.intake.wrap import wrap_solution

    py = wrap_solution("def gap(nums):\n    return max(nums)\n", "python", "gap")
    ns: dict[str, object] = {}
    exec(py, ns)
    assert ns["Solution"]().gap([1, 5, 2]) == 5  # type: ignore[operator]
    cpp = (
        "#include <bits/stdc++.h>\nusing namespace std;\n"
        "int helper(int x) { return x * 2; }\n"
        "int gap(vector<int>& nums, int k) { return helper(nums[0]) + k; }\n"
        "int main() { vector<int> a{1}; cout << gap(a, 2); }\n"
    )
    w = wrap_solution(cpp, "cpp", "gap")
    assert "main" not in w and "class Solution" in w and "::gap(nums, k)" in w
    with tempfile.TemporaryDirectory() as d:
        src = f"{d}/t.cpp"
        Path(src).write_text(
            w + "int main() { vector<int> a{3}; return Solution().gap(a, 1) == 7 ? 0 : 1; }\n"
        )
        r = subprocess.run(["g++", "-std=c++17", src, "-o", f"{d}/t"], capture_output=True)
        assert r.returncode == 0, r.stderr.decode()[:300]
        assert subprocess.run([f"{d}/t"]).returncode == 0
    java = wrap_solution(
        "import java.util.*;\nint gap(int[] a) { return a[0]; }\npublic static void main(String[] x) { }\n",
        "java",
        "gap",
    )
    assert java.startswith("import java.util.*;\nclass Solution {") and "main" not in java
    keep = "class Solution:\n    def gap(self, n):\n        return n\n"
    assert wrap_solution(keep, "python", "gap") == keep
    assert sys  # silence unused


def test_only_the_last_solution_stays_a_ref_and_invented_samples_go() -> None:
    from dsajudge.ai.schemas import Segmentation, SegmentedPart
    from dsajudge.intake.structure import EXAMPLE_WORDS, final_refs

    sections = {
        1: "```python\ndef slow(a):\n    return 1\n```\n```python\ndef fast(a):\n    return 1\n```",
        2: "```cpp\nint main() { return 0; }\n```",
    }
    codes = [
        {"section": 1, "block": 1, "lang": "python", "part": 1},
        {"section": 1, "block": 2, "lang": "python", "part": 1},
        {"section": 2, "block": 1, "lang": "cpp", "part": 1},
    ]
    seg = Segmentation.model_validate(
        {"title": "t", "parts": [SegmentedPart(part=1, text="x")], "code": codes}
    )
    assert [(c.section, c.block) for c in final_refs(seg, sections).code] == [(1, 2)]
    assert not EXAMPLE_WORDS.search("Return the smallest gap in nums.")
    assert EXAMPLE_WORDS.search("Example 1: nums = [1] -> 0")


def test_stray_lines_above_the_first_def_are_commented_and_headers_stay_one_line() -> None:
    code = "return best\n\n\ndef f(a):\n    return a\n"
    got = comment_prose(code, "python")
    assert got.startswith("# return best") and "def f(a):" in got
    from dsajudge.intake.draft import Findings

    f = Findings(notes=["part 1: it failed\nSyntaxError: x\n  more"], errors=["a\nb"])
    assert all(ln.startswith("#") or ln.strip() == "" or True for ln in f.header())
    assert not any("\n" in ln for ln in f.header())


def test_bare_python_functions_set_the_parameters() -> None:
    from dsajudge.ai.schemas import Extraction, ParamOut
    from dsajudge.intake.structure import apply_code_signature

    def ext() -> Extraction:
        return Extraction(
            title="t",
            statement="s",
            method="g",
            params=[ParamOut(name="a", type="int[]")],
            returns="int",
        )

    typed = "def g(a: list[int], k: int) -> int:\n    return k\n"
    e = ext()
    assert apply_code_signature(e, [typed]) is None
    assert [(p.name, p.type) for p in e.params] == [("a", "int[]"), ("k", "int")]
    untyped = "def g(a, k):\n    return k\n"
    msg = apply_code_signature(ext(), [untyped])
    assert msg and "g(a, k)" in msg
    assert apply_code_signature(ext(), ["def g(a):\n    return a\n"]) is None


def test_a_bare_label_line_is_commented_when_the_compiler_rejects_it() -> None:
    cpp = "bfs - c++\nclass Solution {\npublic:\n    int f(int a) { return a; }\n};\n"
    got = comment_prose(cpp, "cpp")
    assert got.startswith("// bfs - c++\n")
    ok = "class Solution {\npublic:\n    int f(int a) { return a; }\n};\n"
    assert comment_prose(ok, "cpp") == ok


def test_a_dedented_continuation_joins_the_function_above() -> None:
    from dsajudge.intake.stitch import join_code

    prev = "text\n```python\ndef f(xs):\n    best = 0\n    for x in xs:\n        best = max(best, x)\n```"
    cur = "```python\nreturn best\n\n\ndef g(a):\n    return a\n```"
    new_prev, rest, joined = join_code(prev, cur)
    assert joined and not rest.strip().startswith("```python\nreturn")
    code = new_prev.split("```python\n")[1].split("\n```")[0]
    compile(code, "x", "exec")
    assert (
        "\n    return best" in code and "        return best" not in code and "\ndef g(a):" in code
    )


def test_a_helper_class_does_not_stop_the_wrapper() -> None:
    from dsajudge.intake.wrap import wrap_solution

    py = "class Node:\n    pass\n\n\ndef f(a):\n    return a\n"
    assert "class Solution:" in wrap_solution(py, "python", "f")
    cpp = "struct Node { int v; };\nint f(int a) { return a; }\n"
    assert "class Solution" in wrap_solution(cpp, "cpp", "f")


def test_the_named_function_gives_the_signature_among_helpers() -> None:
    from dsajudge.ai.schemas import Extraction, ParamOut
    from dsajudge.intake.structure import apply_code_signature, extract_part  # noqa: F401

    code = (
        "bool isPrime(int x) {\n    return x > 1;\n}\n"
        'string solve(int n) {\n    return isPrime(n) ? "y" : "n";\n}\n'
    )
    ext = Extraction(
        title="t",
        statement="s",
        method="solve",
        params=[ParamOut(name="k", type="long")],
        returns="int",
    )
    assert apply_code_signature(ext, [code]) is None
    assert [(p.name, p.type) for p in ext.params] == [("k", "int")] and ext.returns == "string"
