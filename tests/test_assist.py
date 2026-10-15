"""Custom checkers and the AI-written brute / gen.py / checker.py (FakeLLM, real execution)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from dsajudge.ai.assist import (
    AssistContext,
    AssistFailed,
    mutate,
    write_brute,
    write_checker,
    write_gen,
)
from dsajudge.ai.fake import FakeLLM
from dsajudge.cli import app
from dsajudge.config import Config
from dsajudge.gen.manifest import load_manifest
from dsajudge.judge.checkers import CheckerError, part_checker
from dsajudge.judge.custom_checker import CheckerFailure, CustomChecker
from dsajudge.judge.limits import detect_backend
from dsajudge.lang import get_language
from dsajudge.model.loader import load_problem

from .conftest import simple_part, write_problem
from .test_build import TWO_SUM_VALIDATE, two_sum_problem

runner = CliRunner()

ARGMAX_REF = """\
class Solution {
public:
    int findMaxIndex(vector<int>& nums) {
        int b = 0;
        for (int i = 1; i < (int)nums.size(); i++) if (nums[i] > nums[b]) b = i;
        return b;
    }
};
"""
ARGMAX_BRUTE = """\
class Solution:
    def findMaxIndex(self, nums):
        best = 0
        for i in range(len(nums)):
            if nums[i] > nums[best]:
                best = i
        return best
"""
ARGMAX_CHECKER = """\
def check(args, expected, got):
    nums = args[0]
    if isinstance(got, bool) or not isinstance(got, int) or not 0 <= got < len(nums):
        return (False, "not a valid index")
    return (nums[got] == max(nums), "that element is not a maximum")
"""


def argmax_problem(home: Path, **files: str) -> Path:
    part = simple_part(
        1,
        "findMaxIndex",
        [("nums", "int[]")],
        "int",
        [([[3, 9, 2, 9]], 1), ([[5]], 0)],
        constraints={"nums": {"len": [1, 40], "each": [-9, 9]}},
        checker="custom",
        refs=[{"lang": "cpp", "file": "part1/ref/ref.cpp", "origin": "user", "verified": True}],
        brute={"lang": "python", "file": "part1/brute.py", "origin": "user", "verified": True},
        limits={"time_ms": "auto", "memory_mb": 256},
    )
    data = {"schema": 1, "slug": "argmax", "title": "Index of the maximum", "parts": [part]}
    base = {
        "part1/ref/ref.cpp": ARGMAX_REF,
        "part1/brute.py": ARGMAX_BRUTE,
        "part1/checker.py": ARGMAX_CHECKER,
    }
    return write_problem(home / "problems", data, {**base, **files})


def ctx_for(path: Path) -> AssistContext:
    cfg = Config()
    return AssistContext(path, load_problem(path), cfg, detect_backend("poll"))


def code_block(code: str, lang: str = "python") -> str:
    return f"Here you go.\n```{lang}\n{code}```\n"


# ---------------------------------------------------------------- custom checker runtime


def test_custom_checker_worker(tmp_path: Path) -> None:
    script = tmp_path / "checker.py"
    script.write_text(ARGMAX_CHECKER)
    c = CustomChecker(script, Config().judge.python_exe())
    try:
        assert c.check([[1, 5, 5]], 1, 2) == (True, "that element is not a maximum")
        assert c.check([[1, 5, 5]], 1, 0) == (False, "that element is not a maximum")
        assert c.check([[1, 5, 5]], 1, 9) == (False, "not a valid index")
        assert c.check([[1, 5, 5]], 1, "x") == (False, "not a valid index")
    finally:
        c.close()


def test_custom_checker_failures_are_loud(tmp_path: Path) -> None:
    py = Config().judge.python_exe()
    crash = tmp_path / "crash.py"
    crash.write_text("def check(args, expected, got):\n    return 1 / 0\n")
    c = CustomChecker(crash, py)
    with pytest.raises(CheckerFailure, match="ZeroDivisionError"):
        c.check([], 1, 1)
    c.close()
    hang = tmp_path / "hang.py"
    hang.write_text("def check(args, expected, got):\n    while True: pass\n")
    c = CustomChecker(hang, py, timeout=0.5)
    with pytest.raises(CheckerFailure, match="did not answer"):
        c.check([], 1, 1)
    nofn = tmp_path / "nofn.py"
    nofn.write_text("x = 1\n")
    with pytest.raises(CheckerFailure, match="must define check"):
        CustomChecker(nofn, py).check([], 1, 1)
    bare = tmp_path / "bare.py"
    bare.write_text("def check(args, expected, got):\n    return got == expected\n")
    assert CustomChecker(bare, py).check([], 1, 1) == (True, "")


def test_part_checker_custom_needs_the_file(home: Path) -> None:
    path = argmax_problem(home)
    (path / "part1" / "checker.py").unlink()
    part = load_problem(path, check_files=False).part(1)
    with pytest.raises(CheckerError, match=r"checker\.py is missing"):
        part_checker(part, path, Config().judge)
    with pytest.raises(CheckerError, match="problem directory"):
        part_checker(part)


def test_custom_checker_judges_any_valid_answer(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = argmax_problem(home)
    code = runner.invoke(app, ["build", "argmax"])
    assert code.exit_code == 0, code.output
    manifest = load_manifest(path, 1)
    assert manifest and manifest.stress and manifest.oracle == "ref"
    runner.invoke(app, ["open", "argmax", "--no-editor"])
    wd = home / "work" / "argmax" / "part1"
    monkeypatch.chdir(wd)
    sol = wd / get_language("cpp").sol_filename

    def submit(body: str) -> str:
        sol.write_text(
            f"class Solution {{ public: int findMaxIndex(vector<int>& nums) {{ {body} }} }};"
        )
        return runner.invoke(app, ["submit"]).output

    # the LAST maximum is as good as the first one: exact comparison would call it wrong
    out = submit(
        "int b = 0; for (int i = 0; i < (int)nums.size(); i++) "
        "if (nums[i] >= nums[b]) b = i; return b;"
    )
    assert "Accepted" in out, out
    out = submit("return 0;")
    assert "Wrong Answer" in out and "that element is not a maximum" in out, out
    out = submit("return 99;")
    assert "Wrong Answer" in out and "not a valid index" in out


def test_build_rejects_a_ref_the_custom_checker_rejects(home: Path) -> None:
    bad_ref = ARGMAX_REF.replace("nums[i] > nums[b]", "nums[i] < nums[b]")  # finds a minimum
    argmax_problem(home, **{"part1/ref/ref.cpp": bad_ref})
    out = runner.invoke(app, ["build", "argmax"])
    assert out.exit_code == 1 and "disagrees with sample" in out.output


def test_checker_edit_marks_tests_stale(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = argmax_problem(home)
    assert runner.invoke(app, ["build", "argmax"]).exit_code == 0
    runner.invoke(app, ["open", "argmax", "--no-editor"])
    monkeypatch.chdir(home / "work" / "argmax" / "part1")
    (path / "part1" / "checker.py").write_text(ARGMAX_CHECKER + "\n# edited\n")
    out = runner.invoke(app, ["submit"]).output
    assert "out of date" in out


# ---------------------------------------------------------------- mutate


@pytest.mark.parametrize(
    "value", [5, -3, 0, 2.5, True, "abc", "", [1, 2, 3], [], [[1], [2]], ["a", "b"]]
)
def test_mutations_differ_from_the_original(value: Any) -> None:
    for k in range(10):
        bad = mutate(value, k)
        if value in ("", []) and k % 5 == 4:
            continue
        assert bad != value or (value == "" and bad == ""), (value, k)


# ---------------------------------------------------------------- write_brute


def test_write_brute_retries_until_it_reproduces_the_examples(home: Path) -> None:
    part = simple_part(
        1,
        "maxProduct",
        [("nums", "int[]")],
        "int",
        [([[3, 5, -2, 4]], 20), ([[-5, -4, 1]], 20), ([[2, 3]], 6)],
        constraints={"nums": {"len": [2, 20], "each": [-9, 9]}},
    )
    path = write_problem(
        home / "problems", {"schema": 1, "slug": "mp", "title": "t", "parts": [part]}
    )
    wrong = "class Solution:\n    def maxProduct(self, nums):\n        return max(nums) * 2\n"
    broken = "class Solution:\n    def maxProduct(self, nums)\n        return 1\n"
    right = (
        "class Solution:\n    def maxProduct(self, nums):\n        best = None\n"
        "        for i in range(len(nums)):\n            for j in range(i + 1, len(nums)):\n"
        "                p = nums[i] * nums[j]\n                if best is None or p > best:\n"
        "                    best = p\n        return best\n"
    )
    llm = FakeLLM(
        [
            {
                "task": "write_brute",
                "responses": [code_block(wrong), code_block(broken), code_block(right)],
            }
        ]
    )
    ctx = ctx_for(path)
    w = write_brute(llm, ctx, ctx.problem.part(1))
    assert w.attempts == 3 and w.path == "part1/brute.py" and w.verified is False
    assert (path / "part1" / "brute.py").read_text() == right
    # rejections were fed back: first the wrong value, then the syntax error
    assert "it returned" in llm.calls[1].prompt and "sample/1" in llm.calls[1].prompt
    assert "SyntaxError" in llm.calls[2].prompt or "syntax" in llm.calls[2].prompt.lower()
    assert (
        "Statement:" in llm.calls[0].prompt
        and "maxProduct(nums: int[]) -> int" in llm.calls[0].prompt
    )


def test_write_brute_gives_up_after_the_cap_and_keeps_no_file(home: Path) -> None:
    part = simple_part(
        1, "f", [("x", "int")], "int", [([2], 4)], constraints={"x": {"range": [1, 9]}}
    )
    path = write_problem(
        home / "problems", {"schema": 1, "slug": "f", "title": "t", "parts": [part]}
    )
    wrong = "class Solution:\n    def f(self, x):\n        return x\n"
    llm = FakeLLM([{"task": "write_brute", "response": code_block(wrong)}])
    ctx = ctx_for(path)
    with pytest.raises(AssistFailed, match="after 2 attempt"):
        write_brute(llm, ctx, ctx.problem.part(1), attempts=2)
    assert not (path / "part1" / "brute.py").exists()
    assert len(llm.calls) == 2


def test_write_brute_cannot_just_hardcode_the_examples_when_a_ref_exists(home: Path) -> None:
    """With a ref, the brute must also agree with it on generated inputs."""
    part = simple_part(
        1,
        "sumAll",
        [("nums", "int[]")],
        "long",
        [([[1, 2, 3]], 6), ([[-5]], -5)],
        constraints={"nums": {"len": [1, 8], "each": [-50, 50]}},
        refs=[{"lang": "cpp", "file": "part1/ref/ref.cpp", "origin": "user", "verified": True}],
    )
    ref = (
        "class Solution { public: long long sumAll(vector<int>& n) {"
        " long long s = 0; for (int x : n) s += x; return s; } };\n"
    )
    path = write_problem(
        home / "problems",
        {"schema": 1, "slug": "s", "title": "t", "parts": [part]},
        {"part1/ref/ref.cpp": ref},
    )
    cheat = (
        "class Solution:\n    def sumAll(self, nums):\n"
        "        return {(1, 2, 3): 6, (-5,): -5}.get(tuple(nums), 0)\n"
    )
    honest = "class Solution:\n    def sumAll(self, nums):\n        return sum(nums)\n"
    llm = FakeLLM([{"task": "write_brute", "responses": [code_block(cheat), code_block(honest)]}])
    ctx = ctx_for(path)
    w = write_brute(llm, ctx, ctx.problem.part(1))
    assert w.attempts == 2 and w.verified == "stress-only"
    assert "the answer is" in llm.calls[1].prompt


# ---------------------------------------------------------------- write_gen


GOOD_GEN = """\
def generate(rng, size, flavor):
    n = {"edge": 2, "small": rng.randint(2, 8), "medium": rng.randint(2, 100), "max": 1000}[size]
    for _ in range(200):
        vals = rng.sample(range(-10**6, 10**6), n)
        i, j = rng.sample(range(n), 2)
        target = vals[i] + vals[j]
        have = set(vals)
        if sum(1 for x in vals if (target - x) in have and (target - x) != x) // 2 == 1:
            return [vals, target]
    raise RuntimeError("no unique pair")
"""


def test_write_gen_is_verified_against_constraints_and_guarantees(home: Path) -> None:
    path = two_sum_problem(home, {"part1/validate.py": TWO_SUM_VALIDATE})
    dup = "def generate(rng, size, flavor):\n    return [[1, 1], 2]\n"  # not distinct
    two_pairs = (
        "def generate(rng, size, flavor):\n    return [[1, 2, 3, 4], 5]\n"  # 2 pairs sum to 5
    )
    llm = FakeLLM(
        [
            {
                "task": "write_gen",
                "responses": [code_block(dup), code_block(two_pairs), code_block(GOOD_GEN)],
            }
        ]
    )
    ctx = ctx_for(path)
    w = write_gen(llm, ctx, ctx.problem.part(1))
    assert w.attempts == 3 and (path / "part1" / "gen.py").read_text() == GOOD_GEN
    assert "not distinct" in llm.calls[1].prompt
    assert "broke a guarantee" in llm.calls[2].prompt and "pairs" in llm.calls[2].prompt
    assert "exactly one pair" in llm.calls[0].prompt
    # the written generator then lets the part build
    out = runner.invoke(app, ["build", "two-sum"])
    assert out.exit_code == 0, out.output


def test_write_gen_reports_a_crashing_generator(home: Path) -> None:
    path = two_sum_problem(home, {})
    llm = FakeLLM(
        [
            {
                "task": "write_gen",
                "response": code_block(
                    "def generate(rng, size, flavor):\n    raise KeyError('x')\n"
                ),
            }
        ]
    )
    ctx = ctx_for(path)
    with pytest.raises(AssistFailed, match="KeyError"):
        write_gen(llm, ctx, ctx.problem.part(1), attempts=2)
    assert "KeyError" in llm.calls[1].prompt


# ---------------------------------------------------------------- write_checker


def test_write_checker_must_accept_the_oracle_and_reject_mutants(home: Path) -> None:
    path = argmax_problem(home)
    (path / "part1" / "checker.py").unlink()
    yes_man = "def check(args, expected, got):\n    return (True, '')\n"
    too_strict = "def check(args, expected, got):\n    return (got == 123456, 'no')\n"
    negative_index = (
        "def check(args, expected, got):\n    return (args[0][got] == max(args[0]), '')\n"
    )
    llm = FakeLLM(
        [
            {
                "task": "write_checker",
                "responses": [
                    code_block(yes_man),
                    code_block(too_strict),
                    code_block(negative_index),
                    code_block(ARGMAX_CHECKER),
                ],
            }
        ]
    )
    ctx = ctx_for(path)
    part = ctx.problem.part(1)
    w = write_checker(llm, ctx, part, attempts=4)
    assert w.attempts == 4 and (path / "part1" / "checker.py").read_text() == ARGMAX_CHECKER
    assert "accepted the wrong answer" in llm.calls[1].prompt
    assert "rejected the correct answer" in llm.calls[2].prompt
    # `args[0][got]` never raises for got == -1 (Python negative indexing): a mutant slips through
    assert "accepted the wrong answer -1" in llm.calls[3].prompt


def test_write_checker_needs_an_oracle(home: Path) -> None:
    part = simple_part(
        1, "f", [("x", "int")], "int", [([1], 1)], constraints={"x": {"range": [1, 3]}}
    )
    path = write_problem(
        home / "problems", {"schema": 1, "slug": "n", "title": "t", "parts": [part]}
    )
    ctx = ctx_for(path)
    with pytest.raises(AssistFailed, match="neither a ref nor a brute"):
        write_checker(FakeLLM(), ctx, ctx.problem.part(1))
