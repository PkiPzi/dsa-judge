"""Phase 2 integration: `dsa build`, hidden tests, oracles, stress, promote/dispute.

These run real g++, javac and python. The max-subarray fixture is built once per session.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from dsajudge.cli import app
from dsajudge.config import Config, ConfigError, cache_dir, load_config
from dsajudge.gen.manifest import load_manifest
from dsajudge.gen.rng import seed_for
from dsajudge.judge.limits import detect_backend
from dsajudge.lang import get_language
from dsajudge.model.loader import append_ref, load_problem
from dsajudge.oracle.build import BuildError, BuildReport, build_part
from dsajudge.store.bank import db_path
from dsajudge.store.db import DB

from .conftest import FIXTURE_PROBLEM, SOLUTIONS, simple_part, write_problem

runner = CliRunner()
EXT = {"cpp": "cpp", "java": "java", "python": "py"}
LABELS = {
    "AC": "Accepted",
    "WA": "Wrong Answer",
    "WA?": "WA? (unverified oracle)",
    "TLE": "Time Limit Exceeded",
    "RE": "Runtime Error",
    "MLE": "Memory Limit Exceeded",
    "CE": "Compile Error",
}


def dsa(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.output


# ---------------------------------------------------------------- fixtures


@pytest.fixture(scope="session")
def built(
    tmp_path_factory: pytest.TempPathFactory, shared_cache: Path
) -> Iterator[tuple[Path, dict[int, BuildReport]]]:
    """The max-subarray fixture with all three parts built (shared by many tests)."""
    root = tmp_path_factory.mktemp("built")
    dest = root / "max-subarray"
    shutil.copytree(FIXTURE_PROBLEM, dest)
    mp = pytest.MonkeyPatch()
    mp.setenv("DSA_HOME", str(root / "home"))
    mp.setenv("XDG_CACHE_HOME", str(shared_cache))
    try:
        problem = load_problem(dest)
        cfg = Config()
        backend = detect_backend("auto")
        reports = {p.id: build_part(dest, problem, p, cfg, backend) for p in problem.parts}
        yield dest, reports
    finally:
        mp.undo()


@pytest.fixture
def built_problem(home: Path, built: tuple[Path, dict[int, BuildReport]]) -> Path:
    dest = home / "problems" / "max-subarray"
    shutil.copytree(built[0], dest)
    return dest


def open_part(lang: str, part: int = 1, *extra: str) -> None:
    code, out = dsa(
        "open", "max-subarray", "--lang", lang, "--part", str(part), "--no-editor", *extra
    )
    assert code == 0, out


def use_solution(
    monkeypatch: pytest.MonkeyPatch, home: Path, lang: str, part: int, source: Path | str
) -> Path:
    wd = home / "work" / "max-subarray" / f"part{part}"
    sol = wd / get_language(lang).sol_filename
    if isinstance(source, Path):
        shutil.copyfile(source, sol)
    else:
        sol.write_text(source, encoding="utf-8")
    monkeypatch.chdir(wd)
    return sol


def build_cli(slug: str, *extra: str) -> tuple[int, str]:
    return dsa("build", slug, *extra)


# ---------------------------------------------------------------- acceptance: the fixture builds


def test_fixture_builds_every_part(built: tuple[Path, dict[int, BuildReport]]) -> None:
    _, reports = built
    assert [reports[i].oracle for i in (1, 2, 3)] == ["ref", "ref", "brute-only"]
    for i in (1, 2):
        r = reports[i]
        assert r.stress_cases >= 300, "ref is stressed against the brute on 300+ extra seeds"
        assert r.warnings == []
        assert r.counts["max"] == 6 and r.counts["medium_random"] == 10
        assert r.counts["small_random"] >= 35 and r.counts["edge"] >= 10
    assert reports[1].tl_base_ms == 1000.0
    assert "C++" in reports[1].tl_source  # calibrated from the C++ ref
    assert reports[3].tl_base_ms == 1000.0 and "problem.yaml" in reports[3].tl_source
    assert reports[3].stress_cases == 0  # no ref to stress against


def test_manifest_contents(built: tuple[Path, dict[int, BuildReport]]) -> None:
    path, _ = built
    m1, m3 = load_manifest(path, 1), load_manifest(path, 3)
    assert m1 and m3
    assert m1.oracle == "ref" and m1.stress and m1.stress.cases >= 300
    assert {t.group for t in m1.tests} == {"edge", "small_random", "medium_random", "max"}
    assert all(t.expected_sha256 for t in m1.tests)
    # large tests are regenerated from seeds, small ones stored in the bank
    assert all(t.stored for t in m1.tests if t.group != "max")
    assert not any(t.stored for t in m1.tests if t.group == "max")
    stored = list((path / "part1" / "tests" / "small").glob("*.in.json"))
    assert len(stored) == sum(t.stored for t in m1.tests)
    assert len(list((path / "part1" / "tests" / "small").glob("*.out.json"))) == len(stored)
    # brute-only: small tests unverified, max tests run-only
    assert m3.oracle == "brute-only"
    assert all(t.unverified for t in m3.tests if t.expected_sha256)
    assert all(t.expected_sha256 is None for t in m3.tests if t.group == "max")
    assert any(t.expected_sha256 for t in m3.tests if t.group == "small_random")
    # deterministic seeds: seed = H(slug, part, group, index)
    assert all(t.seed == seed_for("max-subarray", 1, t.group, t.index) for t in m1.tests)


# ---------------------------------------------------------------- hidden tests judge solutions

CPP_VARIANTS = ["correct", "wrong", "slow", "crash", "deep_recursion", "memory_hog"]
EXPECTED = {
    "correct": "AC",
    "wrong": "WA",
    "slow": "TLE",
    "crash": "RE",
    "deep_recursion": "AC",
    "memory_hog": "MLE",
}


@pytest.mark.parametrize(
    ("lang", "variant"),
    [("cpp", v) for v in CPP_VARIANTS]
    + [(lang, v) for lang in ("java", "python") for v in ("correct", "wrong", "slow")],
)
def test_variants_against_hidden_tests(
    built_problem: Path, home: Path, monkeypatch: pytest.MonkeyPatch, lang: str, variant: str
) -> None:
    """No manual large cases here: the generated max tests alone separate the variants."""
    open_part(lang)
    use_solution(monkeypatch, home, lang, 1, SOLUTIONS / "part1" / f"{variant}.{EXT[lang]}")
    code, out = dsa("submit", "--lang", lang)
    want = EXPECTED[variant]
    assert LABELS[want] in out.splitlines()[0], out
    assert code == (0 if want == "AC" else 1)
    manifest = load_manifest(built_problem, 1)
    assert manifest
    with DB(db_path()) as db:
        row = db.conn.execute("SELECT verdict, total FROM attempts ORDER BY id DESC").fetchone()
    assert row == (want, 3 + len(manifest.tests))


BIG_ARRAY_BUG = """\
class Solution {
public:
    int maxSubarraySum(vector<int>& nums) {
        if (nums.size() > 20) return -1;  // right on every sample, wrong on big arrays
        long long best = LLONG_MIN, cur = 0;
        for (int x : nums) {
            cur = max<long long>(x, cur + x);
            best = max(best, cur);
        }
        return (int)best;
    }
};
"""


def test_wrong_answer_names_a_generated_test(
    built_problem: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_part("cpp")
    use_solution(monkeypatch, home, "cpp", 1, BIG_ARRAY_BUG)
    code, out = dsa("run")
    assert code == 0 and "Accepted" in out  # samples alone don't expose it
    code, out = dsa("submit")
    assert code == 1 and "Wrong Answer" in out
    m = re.search(r"test: (\S+)", out)
    assert m and re.match(r"medium_random/\d+", m.group(1)), out
    assert "input sizes" in out or "input:" in out


def test_hidden_tests_regenerate_and_detect_damage(
    built_problem: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_part("cpp")
    use_solution(monkeypatch, home, "cpp", 1, SOLUTIONS / "part1" / "correct.cpp")
    # drop every regenerable artifact: large tests come back from their seeds
    for sub in ("tests", "expected"):
        shutil.rmtree(cache_dir() / sub / "max-subarray", ignore_errors=True)
    code, out = dsa("submit")
    assert code == 0 and "Accepted" in out, out
    # a stored test edited by hand is a judge error, never a silent pass
    victim = next((built_problem / "part1" / "tests" / "small").glob("edge-*.in.json"))
    victim.write_text("[1]\n", encoding="utf-8")
    code, out = dsa("submit")
    assert code == 2 and "was modified" in out and "dsa rebuild" in out, out


def test_stale_tests_are_flagged(
    built_problem: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_part("cpp")
    use_solution(monkeypatch, home, "cpp", 1, SOLUTIONS / "part1" / "correct.cpp")
    yaml_path = built_problem / "problem.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8").replace("each: [-1e4, 1e4]", "each: [-9e3, 9e3]", 1),
        encoding="utf-8",
    )
    out = dsa("submit")[1]
    assert "out of date" in out and "dsa build" in out, out


def test_unbuilt_problem_falls_back_to_samples(
    fixture_problem: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_part("cpp")
    use_solution(monkeypatch, home, "cpp", 1, SOLUTIONS / "part1" / "correct.cpp")
    code, out = dsa("submit")
    assert code == 0 and "not built yet" in out and "dsa build max-subarray" in out


# ---------------------------------------------------------------- acceptance: builds that must fail

BUGGY_REF = """\
class Solution {
public:
    int maxSubarraySum(vector<int>& nums) {
        long long best = LLONG_MIN, cur = 0;
        int n = nums.size();
        for (int i = 0; i < max(1, n - 1); i++) {  // BUG: ignores the last element
            cur = max<long long>(nums[i], cur + nums[i]);
            best = max(best, cur);
        }
        return (int)best;
    }
};
"""


def test_deliberately_wrong_ref_is_caught_by_stress(fixture_problem: Path) -> None:
    (fixture_problem / "part1" / "ref" / "ref.cpp").write_text(BUGGY_REF, encoding="utf-8")
    code, out = build_cli("max-subarray", "--part", "1")
    assert code == 1
    assert "stress: ref and brute disagree" in out
    # shrunk to the smallest array where they differ
    assert "nums = [0,1]" in out
    assert "ref   (part1/ref/ref.cpp): 0" in out and "brute (part1/brute.py): 1" in out
    assert not (fixture_problem / "part1" / "tests" / "manifest.json").exists()


def test_ref_that_fails_a_sample_is_rejected(fixture_problem: Path) -> None:
    ref = fixture_problem / "part1" / "ref" / "ref.cpp"
    ref.write_text(ref.read_text().replace("best = LLONG_MIN", "best = 0"), encoding="utf-8")
    code, out = build_cli("max-subarray", "--part", "1")
    assert code == 1 and "disagrees with sample/2" in out and "got 0, expected -3" in out


def test_corrupted_sample_is_caught_by_the_validator(fixture_problem: Path) -> None:
    """10^5 read as 105: a 150-element sample no longer fits the constraint."""
    yaml_path = fixture_problem / "problem.yaml"
    text = yaml_path.read_text(encoding="utf-8")
    text = text.replace(
        "nums: {len: [1, 1e5], each: [-1e4, 1e4]}", "nums: {len: [1, 105], each: [-1e4, 1e4]}", 1
    )
    ones = ", ".join(["1"] * 150)
    text = text.replace(
        "      - {args: [[4]], expected: 4}\n",
        f"      - {{args: [[4]], expected: 4}}\n      - {{args: [[{ones}]], expected: 150}}\n",
        1,
    )
    yaml_path.write_text(text, encoding="utf-8")
    line = next(i for i, ln in enumerate(text.splitlines(), 1) if "nums: {len: [1, 105]" in ln)
    code, out = build_cli("max-subarray", "--part", "1")
    assert code == 1
    assert "sample 4 breaks the constraints" in out
    assert "len 150 is outside [1, 105]" in out
    assert f"problem.yaml:{line}" in out and "constraints.nums" in out
    assert "transcription error" in out


def test_weak_test_warning_when_time_limit_is_very_high(fixture_problem: Path, home: Path) -> None:
    """With a huge limit, even the O(n^2) brute might pass the max tests: warn."""
    yaml_path = fixture_problem / "problem.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8").replace(
            "limits: {time_ms: auto, memory_mb: 256}",
            "limits: {time_ms: 600000, memory_mb: 256}",
            1,
        ),
        encoding="utf-8",
    )
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text("[plan]\nweak_check_cap_s = 3\n", encoding="utf-8")
    code, out = build_cli("max-subarray", "--part", "1")
    assert code == 0, out
    assert "warning:" in out and "weak tests" in out and "time limit" in out
    manifest = load_manifest(fixture_problem, 1)
    assert manifest and manifest.tl_base_ms == 600000.0 and manifest.warnings


SUM_REF = """\
class Solution {
public:
    long long sumAll(vector<int>& nums) {
        long long s = 0;
        for (int x : nums) s += x;
        return s;
    }
};
"""
SUM_BRUTE = """\
class Solution:
    def sumAll(self, nums):
        s = 0
        for x in nums:
            s += x
        return s
"""


def sum_problem(
    home: Path,
    *,
    max_len: int = 300,
    extra_files: dict[str, str] | None = None,
    **part_extra: Any,
) -> Path:
    part_extra.setdefault("constraints", {"nums": {"len": [1, max_len], "each": [-1000, 1000]}})
    part_extra.setdefault("limits", {"time_ms": "auto", "memory_mb": 256})
    part = simple_part(
        1,
        "sumAll",
        [("nums", "int[]")],
        "long",
        [([[1, 2, 3]], 6), ([[-5]], -5)],
        refs=[{"lang": "cpp", "file": "part1/ref/ref.cpp", "origin": "user", "verified": True}],
        brute={"lang": "python", "file": "part1/brute.py", "origin": "user", "verified": True},
        **part_extra,
    )
    data = {"schema": 1, "slug": "sum-all", "title": "Sum", "parts": [part]}
    files = {"part1/ref/ref.cpp": SUM_REF, "part1/brute.py": SUM_BRUTE, **(extra_files or {})}
    return write_problem(home / "problems", data, files)


def test_weak_test_warning_when_the_brute_passes_every_max_test(home: Path) -> None:
    sum_problem(home)  # max length 300: an O(n) brute is as good as the ref
    code, out = build_cli("sum-all")
    assert code == 0, out
    assert "weak tests: the brute (part1/brute.py) passes every max test" in out


def test_no_weak_warning_when_max_tests_defeat_the_brute(
    built: tuple[Path, dict[int, BuildReport]],
) -> None:
    _, reports = built
    assert reports[1].warnings == [] and reports[2].warnings == []


def test_refs_must_agree(home: Path) -> None:
    good = "class Solution:\n    def sumAll(self, nums):\n        return sum(nums)\n"
    bad = (
        "class Solution:\n    def sumAll(self, nums):\n"
        "        return sum(nums) + (1 if len(nums) > 5 else 0)\n"
    )
    path = sum_problem(home, extra_files={"part1/ref/good.py": good, "part1/ref/bad.py": bad})
    append_ref(path, 1, {"lang": "python", "file": "part1/ref/good.py", "origin": "friend"})
    assert build_cli("sum-all")[0] == 0
    append_ref(path, 1, {"lang": "python", "file": "part1/ref/bad.py", "origin": "friend"})
    code, out = build_cli("sum-all")
    assert code == 1 and "refs disagree on" in out and "part1/ref/bad.py" in out


def test_time_limit_is_calibrated_from_the_cpp_ref(home: Path) -> None:
    slow_ref = SUM_REF.replace(
        "long long s = 0;",
        "long long s = 0;\n"
        "        if (nums.size() >= 250) this_thread::sleep_for(chrono::milliseconds(700));",
    )
    sum_problem(home, extra_files={"part1/ref/ref.cpp": slow_ref})
    code, out = build_cli("sum-all")
    assert code == 0, out
    manifest = load_manifest(home / "problems" / "sum-all", 1)
    assert manifest and 2100 <= manifest.tl_base_ms <= 5000, manifest and manifest.tl_base_ms


def test_part_without_any_oracle_cannot_build(home: Path) -> None:
    part = simple_part(
        1, "f", [("x", "int")], "int", [([1], 1)], constraints={"x": {"range": [1, 9]}}
    )
    write_problem(home / "problems", {"schema": 1, "slug": "bare", "title": "t", "parts": [part]})
    code, out = build_cli("bare")
    assert code == 1 and "neither a ref nor a brute" in out


def test_bad_constraints_name_the_param_and_location(home: Path) -> None:
    sum_problem(home, constraints={"nums": {"len": [1, 5], "each": [0, 9], "lenn": 3}})
    code, out = build_cli("sum-all")
    assert code == 1 and "unknown key 'lenn'" in out
    assert "problem.yaml:" in out and "constraints.nums" in out


# ------------------------------------------------------- guarantees need a custom generator

TWO_SUM_REF = """\
class Solution {
public:
    vector<int> twoSum(vector<int>& nums, int target) {
        unordered_map<long long, int> seen;
        for (int i = 0; i < (int)nums.size(); i++) {
            auto it = seen.find((long long)target - nums[i]);
            if (it != seen.end()) return {it->second, i};
            seen[nums[i]] = i;
        }
        return {};
    }
};
"""
TWO_SUM_BRUTE = """\
class Solution:
    def twoSum(self, nums, target):
        n = len(nums)
        for i in range(n):
            for j in range(i + 1, n):
                if nums[i] + nums[j] == target:
                    return [i, j]
        return []
"""
TWO_SUM_GEN = """\
def generate(rng, size, flavor):
    n = {"edge": 2, "small": rng.randint(2, 8), "medium": rng.randint(2, 100), "max": 1000}[size]
    for _ in range(100):
        vals = rng.sample(range(-10**6, 10**6), n)
        i, j = rng.sample(range(n), 2)
        target = vals[i] + vals[j]
        have = set(vals)
        pairs = sum(1 for x in vals if (target - x) in have and (target - x) != x) // 2
        if pairs == 1:
            return [vals, target]
    raise RuntimeError("no unique pair found")
"""
TWO_SUM_VALIDATE = """\
def validate(args):
    nums, target = args
    have = set(nums)
    pairs = sum(1 for x in nums if (target - x) in have and (target - x) != x) // 2
    return None if pairs == 1 else f"{pairs} pairs sum to the target, expected exactly one"
"""


def two_sum_problem(home: Path, extra: dict[str, str]) -> Path:
    part = simple_part(
        1,
        "twoSum",
        [("nums", "int[]"), ("target", "int")],
        "int[]",
        [([2, 7, 11, 15], 9)],
        constraints={
            "nums": {"len": [2, 1000], "each": [-1000000, 1000000], "distinct": True},
            "target": {"range": [-2000000, 2000000]},
        },
        guarantees=["exactly one pair of elements sums to target"],
        checker="unordered",
        refs=[{"lang": "cpp", "file": "part1/ref/ref.cpp", "origin": "user", "verified": True}],
        brute={"lang": "python", "file": "part1/brute.py", "origin": "user", "verified": True},
    )
    # sample answers are indices [0, 1]
    part["samples"] = [{"args": [[2, 7, 11, 15], 9], "expected": [0, 1]}]
    data = {"schema": 1, "slug": "two-sum", "title": "Two sum", "parts": [part]}
    return write_problem(
        home / "problems",
        data,
        {"part1/ref/ref.cpp": TWO_SUM_REF, "part1/brute.py": TWO_SUM_BRUTE, **extra},
    )


def test_guarantees_force_a_custom_generator(home: Path) -> None:
    two_sum_problem(home, {})
    code, out = build_cli("two-sum")
    assert code == 1 and "guarantees" in out and "part1/gen.py" in out


def test_custom_generator_and_validator(home: Path) -> None:
    path = two_sum_problem(
        home, {"part1/gen.py": TWO_SUM_GEN, "part1/validate.py": TWO_SUM_VALIDATE}
    )
    code, out = build_cli("two-sum")
    assert code == 0, out
    manifest = load_manifest(path, 1)
    assert manifest and manifest.generator == "custom" and manifest.stress
    for t in manifest.tests:
        if t.stored:
            nums, _target = [
                json.loads(ln)
                for ln in (path / "part1" / "tests" / "small" / f"{t.id.replace('/', '-')}.in.json")
                .read_text()
                .splitlines()
            ]
            assert len(nums) == len(set(nums))


def test_custom_generator_that_breaks_the_validator_is_rejected(home: Path) -> None:
    bad_gen = "def generate(rng, size, flavor):\n    return [[1, 1, 2], 3]\n"
    two_sum_problem(home, {"part1/gen.py": bad_gen, "part1/validate.py": TWO_SUM_VALIDATE})
    code, out = build_cli("two-sum")
    assert code == 1 and "invalid input" in out and "not distinct" in out


def test_custom_generator_crash_is_reported(home: Path) -> None:
    two_sum_problem(
        home, {"part1/gen.py": "def generate(rng, size, flavor):\n    raise ValueError('boom')\n"}
    )
    code, out = build_cli("two-sum")
    assert code == 1 and "boom" in out and "gen.py" in out


# ---------------------------------------------------------------- brute-only: promote and dispute

CIRCULAR_CPP = """\
class Solution {
public:
    int maxCircularSubarraySum(vector<int>& nums) {
        long long total = 0, curMax = 0, curMin = 0, best = LLONG_MIN, worst = LLONG_MAX;
        for (int x : nums) {
            total += x;
            curMax = max<long long>(x, curMax + x);
            best = max(best, curMax);
            curMin = min<long long>(x, curMin + x);
            worst = min(worst, curMin);
        }
        if (best < 0) return (int)best;
        return (int)max(best, total - worst);
    }
};
"""
# Right on every sample (all have at most 5 elements), wrong on bigger arrays.
CIRCULAR_FLAWED_CPP = CIRCULAR_CPP.replace(
    "        if (best < 0)", "        if (nums.size() > 8) return (int)best;\n        if (best < 0)"
)


def test_brute_only_part_gives_unverified_verdicts(
    built_problem: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_part("cpp", 3, "--force")
    use_solution(monkeypatch, home, "cpp", 3, CIRCULAR_FLAWED_CPP)
    code, out = dsa("submit")
    assert code == 1 and "WA? (unverified oracle)" in out, out
    assert "expected:" in out and "got:" in out
    use_solution(monkeypatch, home, "cpp", 3, CIRCULAR_CPP)
    code, out = dsa("submit")
    assert code == 0 and "Accepted" in out, out
    code, out = dsa("show", "max-subarray")
    assert "brute-only" in out


def failing_ids(out: str) -> list[str]:
    return re.findall(r"test: (\S+)", out)


def test_dispute_makes_a_test_run_only(
    built_problem: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_part("cpp", 3, "--force")
    use_solution(monkeypatch, home, "cpp", 3, CIRCULAR_FLAWED_CPP)
    code, out = dsa("submit", "--all")
    before = failing_ids(out)
    assert code == 1 and len(before) >= 2, out
    victim = before[0]
    code, out = dsa("dispute", victim, "--note", "I think the brute is wrong here")
    assert code == 0 and "needs review" in out
    assert "needs-review" in dsa("show", "max-subarray")[1]
    code, out = dsa("submit", "--all")
    assert "disputed" in out
    assert victim not in failing_ids(out)
    assert failing_ids(out) == before[1:]
    # samples can't be disputed; unknown tests are refused; --clear withdraws
    assert dsa("dispute", "sample/1")[0] == 2
    assert dsa("dispute", "small_random/99")[0] == 2
    code, out = dsa("dispute", victim, "--clear")
    assert code == 0 and "cleared" in out
    assert "needs-review" not in dsa("show", "max-subarray")[1]
    assert failing_ids(dsa("submit", "--all")[1]) == before


def test_dispute_only_applies_to_brute_only_parts(
    built_problem: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_part("cpp")
    use_solution(monkeypatch, home, "cpp", 1, SOLUTIONS / "part1" / "correct.cpp")
    code, out = dsa("dispute", "small_random/01")
    assert code == 2 and "not brute-only" in out


def test_promote_turns_an_accepted_solution_into_the_ref(
    built_problem: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_part("cpp", 3, "--force")
    use_solution(monkeypatch, home, "cpp", 3, CIRCULAR_FLAWED_CPP)
    # never accepted: refused
    code, out = dsa("promote")
    assert code == 2 and "not been accepted" in out
    use_solution(monkeypatch, home, "cpp", 3, CIRCULAR_CPP)
    assert dsa("submit")[0] == 0
    code, out = dsa("promote")
    assert code == 0 and "promoted" in out and "oracle promoted" in out, out
    manifest = load_manifest(built_problem, 3)
    assert manifest and manifest.oracle == "promoted" and manifest.stress
    assert all(t.expected_sha256 for t in manifest.tests)  # max tests have full verdicts now
    assert not any(t.unverified for t in manifest.tests)
    assert (built_problem / "part3" / "ref" / "promoted.cpp").is_file()
    problem = load_problem(built_problem)
    assert problem.part(3).refs[0].origin == "promoted"
    assert problem.part(3).derived_oracle() == "promoted"
    # the flawed solution now fails with a plain WA, not WA?
    use_solution(monkeypatch, home, "cpp", 3, CIRCULAR_FLAWED_CPP)
    code, out = dsa("submit")
    assert code == 1 and "Wrong Answer" in out and "WA?" not in out, out
    # promoting twice is refused
    use_solution(monkeypatch, home, "cpp", 3, CIRCULAR_CPP)
    code, out = dsa("promote")
    assert code == 2 and "already has a ref" in out


def test_promote_rolls_back_when_the_solution_disagrees_with_the_brute(
    built_problem: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_part("cpp", 3, "--force")
    use_solution(monkeypatch, home, "cpp", 3, CIRCULAR_CPP)
    assert dsa("submit")[0] == 0
    # the brute is edited after the AC and now disagrees with the accepted solution
    brute = built_problem / "part3" / "brute.py"
    brute.write_text(brute.read_text(encoding="utf-8").replace("return best", "return best + 1"))
    yaml_before = (built_problem / "problem.yaml").read_text(encoding="utf-8")
    manifest_before = (built_problem / "part3" / "tests" / "manifest.json").read_text()
    code, out = dsa("promote")
    assert code == 2 and "does not hold up as a ref" in out and "Nothing was changed" in out, out
    assert (built_problem / "problem.yaml").read_text(encoding="utf-8") == yaml_before
    assert not (built_problem / "part3" / "ref" / "promoted.cpp").exists()
    assert (built_problem / "part3" / "tests" / "manifest.json").read_text() == manifest_before


# ---------------------------------------------------------------- CLI surface


def test_cli_build_rebuild_and_show(fixture_problem: Path, home: Path) -> None:
    code, out = build_cli("max-subarray", "--part", "3")
    assert code == 0, out
    assert "oracle brute-only" in out and "built" in out
    code, out = dsa("show", "max-subarray")
    assert "hidden" in out and "not built" in out  # part 3 built, parts 1 and 2 not
    stored = fixture_problem / "part3" / "tests" / "small"
    assert any(stored.iterdir())
    code, out = dsa("rebuild", "max-subarray", "--part", "3")
    assert code == 0 and "oracle brute-only" in out
    assert dsa("build", "max-subarray", "--part", "9")[0] == 2


def test_dsa_build_gives_ref_ref_brute_only(fixture_problem: Path) -> None:
    """Acceptance: `dsa build max-subarray` builds parts 1-2 in ref mode, part 3 brute-only."""
    code, out = build_cli("max-subarray")
    assert code == 0, out
    assert out.count("oracle ref") == 2 and out.count("oracle brute-only") == 1
    assert "warning:" not in out
    assert [load_manifest(fixture_problem, i).oracle for i in (1, 2, 3)] == [  # type: ignore[union-attr]
        "ref",
        "ref",
        "brute-only",
    ]


def test_plan_config_from_toml(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text("[plan]\nsmall_random = 5\nmax = 2\n", encoding="utf-8")
    cfg = load_config()
    assert cfg.plan.small_random == 5 and cfg.plan.max == 2 and cfg.plan.medium_random == 10
    (home / "config.toml").write_text("[plan]\nsmall_randm = 5\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="small_randm"):
        load_config()


def test_build_error_type_is_exposed() -> None:
    assert issubclass(BuildError, Exception)
