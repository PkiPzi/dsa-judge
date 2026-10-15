"""Integration: Phase 1 acceptance on the max-subarray fixture, through the CLI."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from ruamel.yaml import YAML
from typer.testing import CliRunner

from dsajudge.cli import app
from dsajudge.lang import get_language
from dsajudge.store.bank import db_path
from dsajudge.store.db import DB

from .conftest import SOLUTIONS
from .make_large_cases import write_cases

runner = CliRunner()
EXPECTED = YAML(typ="safe").load((SOLUTIONS / "part1" / "expected.yaml").read_text())
EXT = {"cpp": "cpp", "java": "java", "python": "py"}
LABELS = {
    "AC": "Accepted",
    "WA": "Wrong Answer",
    "TLE": "Time Limit Exceeded",
    "RE": "Runtime Error",
    "MLE": "Memory Limit Exceeded",
}


def dsa(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.output


def open_part(lang: str, part: int = 1, *extra: str) -> Path:
    code, out = dsa(
        "open", "max-subarray", "--lang", lang, "--part", str(part), "--no-editor", *extra
    )
    assert code == 0, out
    return Path.cwd()


def use(monkeypatch: pytest.MonkeyPatch, home: Path, lang: str, part: int, variant: str) -> Path:
    wd = home / "work" / "max-subarray" / f"part{part}"
    sol = wd / get_language(lang).sol_filename
    shutil.copyfile(SOLUTIONS / f"part{part}" / f"{variant}.{EXT[lang]}", sol)
    monkeypatch.chdir(wd)
    return sol


@pytest.fixture
def large_fixture(fixture_problem: Path) -> Path:
    write_cases(fixture_problem, [1, 2])
    return fixture_problem


@pytest.mark.parametrize("variant", sorted(EXPECTED))
@pytest.mark.parametrize("lang", ["cpp", "java", "python"])
def test_variant_verdicts(
    large_fixture: Path, home: Path, monkeypatch: pytest.MonkeyPatch, lang: str, variant: str
) -> None:
    open_part(lang)
    use(monkeypatch, home, lang, 1, variant)
    code, out = dsa("submit", "--lang", lang)
    expected = EXPECTED[variant]
    assert LABELS[expected] in out.splitlines()[0], out
    assert code == (0 if expected == "AC" else 1)
    with DB(db_path()) as db:
        row = db.conn.execute("SELECT verdict, total FROM attempts ORDER BY id DESC").fetchone()
    assert row == (expected, 7)


def test_unlock_flow(large_fixture: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out = dsa("open", "max-subarray", "--part", "2", "--no-editor")
    assert code == 2 and "locked" in out

    open_part("cpp")
    use(monkeypatch, home, "cpp", 1, "wrong")
    assert dsa("submit")[0] == 1
    code, out = dsa("open", "max-subarray", "--part", "2", "--no-editor")
    assert code == 2, "a wrong submit must not unlock part 2"

    sol = use(monkeypatch, home, "cpp", 1, "correct")
    code, out = dsa("submit")
    assert code == 0 and "part 2 unlocked" in out

    code, out = dsa("open", "max-subarray", "--no-editor")  # defaults to the unlocked part
    assert code == 0 and "part 2" in out
    p2 = home / "work" / "max-subarray" / "part2"
    assert (p2 / "prev.cpp").read_text() == sol.read_text()
    assert "maxSumWithOneDeletion" in (p2 / "sol.cpp").read_text()

    # Part 2's expected outputs come from the Java ref through the Java driver.
    use(monkeypatch, home, "cpp", 2, "correct")
    code, out = dsa("submit")
    assert code == 0, out
    assert "passed 7/7" in out

    code, out = dsa("show")
    assert code == 0
    assert out.count("AC") >= 2

    # part 3 is now open; part 3 is brute-only
    code, out = dsa("list", "--part-status")
    assert code == 0 and "max-subarray" in out


def test_force_peek(fixture_problem: Path, home: Path) -> None:
    code, out = dsa("open", "max-subarray", "--part", "3", "--force", "--no-editor")
    assert code == 0 and "peeking" in out
    with DB(db_path()) as db:
        assert db.unlocked_part("max-subarray", 3) == 1


def _set_refs(problem_dir: Path, part: int, refs: list[dict[str, str]]) -> None:
    y = YAML()
    path = problem_dir / "problem.yaml"
    data = y.load(path.read_text())
    data["parts"][part - 1]["refs"] = refs
    with path.open("w") as fh:
        y.dump(data, fh)


def test_python_ref_runs_through_driver(
    large_fixture: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = large_fixture / "part1" / "ref" / "solution.py"
    shutil.copyfile(SOLUTIONS / "part1" / "correct.py", ref)
    _set_refs(
        large_fixture, 1, [{"lang": "python", "file": "part1/ref/solution.py", "origin": "friend"}]
    )
    open_part("cpp")
    use(monkeypatch, home, "cpp", 1, "correct")
    code, out = dsa("submit", "--all")
    assert code == 0 and "passed 7/7" in out, out
    # no C++ ref -> default 1000 ms base limit
    assert "limit 1000" in out


def test_refs_must_agree(large_fixture: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ref = large_fixture / "part1" / "ref" / "solution.py"
    ref.write_text(
        "class Solution:\n"
        "    def maxSubarraySum(self, nums):\n"
        "        if len(nums) > 7:\n"
        "            return 0\n"
        "        best, cur = nums[0], 0\n"
        "        for x in nums:\n"
        "            cur = max(x, cur + x)\n"
        "            best = max(best, cur)\n"
        "        return best\n"
    )
    _set_refs(
        large_fixture,
        1,
        [
            {"lang": "cpp", "file": "part1/ref/ref.cpp", "origin": "user"},
            {"lang": "python", "file": "part1/ref/solution.py", "origin": "friend"},
        ],
    )
    open_part("cpp")
    use(monkeypatch, home, "cpp", 1, "correct")
    code, out = dsa("submit")
    assert code == 2
    assert "refs disagree on manual/large-01-random" in out


def test_ref_must_match_samples(
    fixture_problem: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (fixture_problem / "part1" / "ref" / "ref.cpp").write_text(
        "class Solution { public: int maxSubarraySum(vector<int>& nums) { return 0; } };\n"
    )
    open_part("cpp")
    use(monkeypatch, home, "cpp", 1, "correct")
    code, out = dsa("run")
    assert code == 2
    assert "disagrees with sample/1" in out


def test_brute_only_part(
    fixture_problem: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manual = fixture_problem / "part3" / "tests" / "manual"
    manual.mkdir(parents=True)
    (manual / "small-1.in.json").write_text("[3,-1,-1,-1,-1,4]\n")
    (manual / "big-1.in.json").write_text("[" + ",".join(["1"] * 30000) + "]\n")
    open_part("python", 3, "--force")
    wd = home / "work" / "max-subarray" / "part3"
    (wd / "sol.py").write_text(
        "class Solution:\n"
        "    def maxCircularSubarraySum(self, nums):\n"
        "        if len(nums) > 5:\n"
        "            return -1\n"
        "        n, best = len(nums), nums[0]\n"
        "        for s in range(n):\n"
        "            t = 0\n"
        "            for k in range(n):\n"
        "                t += nums[(s + k) % n]\n"
        "                best = max(best, t)\n"
        "        return best\n"
    )
    monkeypatch.chdir(wd)
    code, out = dsa("submit", "--all")
    assert code == 1
    assert "WA? (unverified oracle)" in out
    assert "manual/small-1" in out
    assert "run-only" in out
    # the big test is run-only, so returning -1 there still "passes"
    assert "passed 4/5" in out
