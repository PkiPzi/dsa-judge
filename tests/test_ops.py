"""Design problems (mode: ops): schema, generator, validator, shrinking and the judge itself."""

from __future__ import annotations

import copy
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from ruamel.yaml import YAML
from typer.testing import CliRunner

from dsajudge.cli import app
from dsajudge.config import Config, PlanConfig
from dsajudge.gen.manifest import load_manifest
from dsajudge.gen.ops import OpsGenerator
from dsajudge.gen.plan import make_plan, stress_plan
from dsajudge.gen.validator import validate_ops
from dsajudge.judge.limits import detect_backend
from dsajudge.lang import get_language
from dsajudge.model.constraints import ConstraintError, parse_design_constraints
from dsajudge.model.design import (
    check_sequence,
    describe_difference,
    initial_counters,
    replay_counters,
)
from dsajudge.model.loader import load_problem
from dsajudge.model.schema import Part, Problem
from dsajudge.oracle.build import build_part
from dsajudge.oracle.shrink import ops_candidates, shrink

from .conftest import FIXTURES

MIN_STACK = FIXTURES / "problems" / "min-stack"
LRU = FIXTURES / "problems" / "lru-cache"
VARIANTS = FIXTURES / "solutions" / "min-stack" / "part1"
EXPECTED = YAML(typ="safe").load((VARIANTS / "expected.yaml").read_text())
EXT = {"cpp": "cpp", "java": "java", "python": "py"}
LABELS = {
    "AC": "Accepted",
    "WA": "Wrong Answer",
    "TLE": "Time Limit Exceeded",
    "RE": "Runtime Error",
}
runner = CliRunner()


def dsa(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.output


def part_of(path: Path) -> Part:
    return load_problem(path).parts[0]


def raw(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = YAML(typ="safe").load((path / "problem.yaml").read_text())
    return copy.deepcopy(data)


# ------------------------------------------------------------------ schema


def test_fixtures_load() -> None:
    ms, lru = part_of(MIN_STACK), part_of(LRU)
    assert ms.mode == "ops" and ms.dsg.class_name == "MinStack" and ms.signature is None
    assert [m.name for m in ms.dsg.methods] == ["push", "pop", "top", "getMin"]
    assert ms.wire_names == ["ops", "args"]
    assert [r.lang for r in ms.refs] == ["cpp", "java", "python"]
    assert [p.name for p in lru.dsg.constructor] == ["capacity"]
    assert ms.derived_oracle() == "ref"


def mutate(path: Path, fn: Any) -> Problem:
    data = raw(path)
    fn(data["parts"][0])
    return Problem.model_validate(data)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda p: p.pop("design"), "needs a design"),
        (
            lambda p: p.update(signature={"method": "f", "params": [], "returns": "int"}),
            "not signature",
        ),
        (lambda p: p.update(input_format="{x}"), "stdio"),
        (lambda p: p.update(checker="unordered"), "ordered"),
        (lambda p: p["samples"][0].update(args=[["MinStack"]]), "two lists"),
        (lambda p: p["samples"][0]["args"][0].__setitem__(0, "Other"), "class name"),
        (lambda p: p["samples"][0]["args"][0].__setitem__(1, "nope"), "unknown method"),
        (lambda p: p["samples"][0]["args"][1].__setitem__(1, []), "takes 1 argument"),
        (lambda p: p["samples"][0]["args"][1].__setitem__(1, ["x"]), "expected an integer"),
        (lambda p: p["samples"][0]["expected"].pop(), "result(s) for 8 operation"),
        (lambda p: p["samples"][0]["expected"].__setitem__(1, 5), "returns void, expected null"),
        (lambda p: p["samples"][0]["expected"].__setitem__(0, 1), "constructor"),
        (lambda p: p["samples"][0]["expected"].__setitem__(4, "x"), "expected an integer"),
        (lambda p: p["design"]["methods"].append({"name": "calls"}), "reserved"),
        (lambda p: p["design"]["methods"].append({"name": "pop"}), "duplicate method"),
        (lambda p: p["design"]["methods"].append({"name": "MinStack"}), "same name as the class"),
        (lambda p: p["design"]["methods"][1].update(requires={"nope": 1}), "unknown counter"),
        (lambda p: p["design"].update(counters={"size": "len(xs)"}), "not a constructor param"),
        (lambda p: p["design"].update(**{"class": "1bad"}), "valid identifier"),
        (lambda p: p["design"].update(methods=[]), "at least one method"),
    ],
)
def test_ops_schema_errors(change: Any, message: str) -> None:
    with pytest.raises(ValidationError, match=re.escape(message)):
        mutate(MIN_STACK, change)


def test_design_is_only_for_ops_parts() -> None:
    with pytest.raises(ValidationError, match="only for mode: ops"):
        mutate(MIN_STACK, lambda p: p.update(mode="function"))


# ------------------------------------------------------------------ constraints


def constraints_error(path: Path, change: Any) -> str:
    data = raw(path)
    change(data["parts"][0]["constraints"])
    part = Problem.model_validate(data).parts[0]
    with pytest.raises(ConstraintError) as e:
        parse_design_constraints(part)
    return str(e.value)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda c: c.pop("calls"), "missing 'len'"),
        (lambda c: c["calls"].update(lenn=3), "unknown key 'lenn'"),
        (lambda c: c.update(frobnicate={}), "not a method of MinStack"),
        (lambda c: c["calls"].update(mix={"push": 1, "bogus": 2}), "'bogus' is not a method"),
        (lambda c: c["calls"].update(mix={"push": 0}), "weight above 0"),
        (lambda c: c["calls"].update(mix={"push": -1}), "whole number"),
        (lambda c: c.pop("push"), "no constraint given"),
        (lambda c: c["push"].update(extra={"range": [0, 1]}), "not a parameter of push"),
        (lambda c: c["push"]["val"].update(range=[0, "later"]), "no parameter named 'later'"),
    ],
)
def test_constraint_errors(change: Any, message: str) -> None:
    assert message in constraints_error(MIN_STACK, change)


def test_constructor_param_needs_a_constraint() -> None:
    assert "capacity" in constraints_error(LRU, lambda c: c.pop("ctor"))


# ------------------------------------------------------------------ generation


PLAN = PlanConfig()


@pytest.mark.parametrize("fixture", [MIN_STACK, LRU])
def test_every_planned_test_is_valid_and_deterministic(fixture: Path) -> None:
    part = part_of(fixture)
    specs = parse_design_constraints(part)
    gen = OpsGenerator(specs, PLAN)
    tests = [*make_plan("demo", 1, PLAN), *stress_plan("demo", 1, 60)]
    for t in tests:
        args = gen.generate(t)
        assert validate_ops(specs, args) == [], (t, args)
        assert check_sequence(part.dsg, args[0], args[1]) is None
        assert replay_counters(part.dsg, args[0], args[1]) is None
        assert args == OpsGenerator(specs, PLAN).generate(t)  # same seed, same input


def test_sizes_and_flavors_shape_the_sequence() -> None:
    specs = parse_design_constraints(part_of(MIN_STACK))
    gen = OpsGenerator(specs, PLAN)
    by = {(t.group, t.size, t.flavor): t for t in make_plan("demo", 1, PLAN)}
    n = lambda test: len(gen.generate(test)[0]) - 1  # noqa: E731
    assert n(by[("edge", "min", "all_min")]) == 1  # calls.len lower bound
    assert n(by[("max", "max", "random")]) == 100_000

    def peak(test: Any) -> int:
        ops, _ = gen.generate(test)
        depth = best = 0
        for o in ops[1:]:
            depth += {"push": 1, "pop": -1}.get(o, 0)
            best = max(best, depth)
        return best

    # `asc` makes the structure big; `random` keeps the mix
    assert peak(by[("max", "max", "asc")]) > 2 * peak(by[("max", "max", "random")])
    ops, _ = gen.generate(by[("max", "max", "desc")])
    half = len(ops) // 2
    assert ops[1:half].count("push") > ops[half:].count("push")  # builds, then drains


def test_arguments_depend_on_constructor_values() -> None:
    specs = parse_design_constraints(part_of(LRU))
    gen = OpsGenerator(specs, PLAN)
    for t in make_plan("demo", 1, PLAN):
        (capacity,), calls = gen.generate(t)[1][0], gen.generate(t)[1][1:]
        assert all(0 <= a[0] <= min(10_000, 2 * capacity) for a in calls)


def test_generator_stops_when_nothing_is_allowed() -> None:
    data = raw(MIN_STACK)
    data["parts"][0]["constraints"]["calls"]["mix"] = {"pop": 1}  # pop needs size >= 1
    part = Problem.model_validate(data).parts[0]
    specs = parse_design_constraints(part)
    test = next(t for t in make_plan("demo", 1, PLAN) if t.size == "max")
    ops, args = OpsGenerator(specs, PLAN).generate(test)
    assert ops == ["MinStack"] and args == [[]]
    assert "calls" in validate_ops(specs, [ops, args])[0]  # too few calls: reported, not hidden


def test_validator_rejects_bad_sequences() -> None:
    specs = parse_design_constraints(part_of(MIN_STACK))

    def bad(ops: list[str], args: list[Any]) -> str:
        errs = validate_ops(specs, [ops, args])
        assert errs, (ops, args)
        return errs[0]

    assert validate_ops(specs, [["MinStack", "push", "top"], [[], [3], []]]) == []
    assert "needs size >= 1" in bad(["MinStack", "pop"], [[], []])
    assert "first operation" in bad(["Nope", "push"], [[], [1]])
    assert "unknown method" in bad(["MinStack", "peek"], [[], []])
    assert "val" in bad(["MinStack", "push"], [[], [10**6]])  # outside [-1e4, 1e4]
    assert "takes 1 argument" in bad(["MinStack", "push"], [[], []])
    assert "1 operation(s) but 2" in bad(["MinStack"], [[], []])
    assert "expected 2 argument" in validate_ops(specs, [["MinStack"]])[0]
    big = ["MinStack"] + ["push"] * 100_001
    assert "outside [1, 100000]" in bad(big, [[]] + [[1]] * 100_001)


def test_counters_can_start_from_constructor_values() -> None:
    data = raw(MIN_STACK)
    d = data["parts"][0]["design"]
    d["constructor"] = [{"name": "xs", "type": "int[]"}]
    d["counters"] = {"size": "len(xs)"}
    data["parts"][0]["constraints"]["ctor"] = {"xs": {"len": [0, 5], "each": [0, 9]}}
    data["parts"][0]["samples"] = []
    part = Problem.model_validate(data).parts[0]
    assert initial_counters(part.dsg, [[4, 5, 6]]) == {"size": 3}
    specs = parse_design_constraints(part)
    for t in make_plan("demo", 1, PLAN):
        args = OpsGenerator(specs, PLAN).generate(t)
        assert validate_ops(specs, args) == []


def test_describe_difference() -> None:
    ops = ["S", "push", "top", "top"]
    args = [[], [3], [], []]
    assert describe_difference(ops, args, [None, None, 3, 3], [None, None, 3, 3]) is None
    msg = describe_difference(ops, args, [None, None, 3, 3], [None, None, 3, 4])
    assert msg == "call #3 top() returned 4, expected 3"
    assert "2 result(s)" in (describe_difference(ops, args, [None] * 4, [None, None]) or "")
    assert "not a list" in (describe_difference(ops, args, [None] * 4, 5) or "")
    close = describe_difference(ops, args, [1.0], [1.0000001], lambda e, g: abs(e - g) < 1e-3)
    assert close is None


# ------------------------------------------------------------------ shrinking


def test_ops_candidates_keep_the_constructor_and_shape() -> None:
    args = [["S", "push", "push", "top", "pop"], [[], [5], [9], [], []]]
    cands = ops_candidates(args)
    assert cands and all(c[0][0] == "S" and len(c[0]) == len(c[1]) for c in cands)
    assert all(len(c[0]) <= len(args[0]) for c in cands)
    assert [["S", "push", "push"], [[], [5], [9]]] in cands  # tail removed


def test_shrink_reduces_a_call_sequence() -> None:
    specs = parse_design_constraints(part_of(MIN_STACK))
    gen = OpsGenerator(specs, PLAN)
    test = next(t for t in make_plan("demo", 1, PLAN) if t.group == "medium_random")
    args = gen.generate(test)
    assert len(args[0]) > 50

    def fails(cands: list[list[Any]]) -> list[bool]:  # "a getMin directly after a pop"
        return [
            any(a == "pop" and b == "getMin" for a, b in zip(c[0], c[0][1:], strict=False))
            for c in cands
        ]

    def valid(cands: list[list[Any]]) -> list[bool]:
        return [not validate_ops(specs, c) for c in cands]

    assert fails([args]) == [True]
    small = shrink(args, valid, fails, ops_candidates)
    assert len(small[0]) <= 6  # constructor, push, push, pop, getMin (plus at most one extra)
    assert fails([small]) == [True] and validate_ops(specs, small) == []


# ------------------------------------------------------------------ integration


@pytest.fixture(scope="session")
def built_min_stack(tmp_path_factory: pytest.TempPathFactory, shared_cache: Path) -> Iterator[Path]:
    """The min-stack fixture built once: three refs in three languages, stressed on a brute."""
    root = tmp_path_factory.mktemp("ops-built")
    dest = root / "min-stack"
    shutil.copytree(MIN_STACK, dest)
    mp = pytest.MonkeyPatch()
    mp.setenv("DSA_HOME", str(root / "home"))
    mp.setenv("XDG_CACHE_HOME", str(shared_cache))
    try:
        problem = load_problem(dest)
        report = build_part(dest, problem, problem.parts[0], Config(), detect_backend("auto"))
        assert report.oracle == "ref" and report.stress_cases > 300
        assert report.warnings == []  # the max tests defeat the brute
        yield dest
    finally:
        mp.undo()


@pytest.fixture
def min_stack(home: Path, built_min_stack: Path) -> Path:
    dest = home / "problems" / "min-stack"
    shutil.copytree(built_min_stack, dest)
    return dest


def use(monkeypatch: pytest.MonkeyPatch, home: Path, slug: str, lang: str, source: Path) -> Path:
    dsa("open", slug, "--lang", lang, "--no-editor")
    wd = home / "work" / slug / "part1"
    sol = wd / get_language(lang).sol_filename
    shutil.copyfile(source, sol)
    monkeypatch.chdir(wd)
    return sol


def test_manifest_records_the_ops_mode(min_stack: Path) -> None:
    m = load_manifest(min_stack, 1)
    assert m and m.mode == "ops" and m.oracle == "ref" and len(m.refs) == 3
    assert m.counts()["max"] == 6


@pytest.mark.parametrize("lang", ["cpp", "java", "python"])
@pytest.mark.parametrize("variant", sorted(EXPECTED))
def test_variant_verdicts(
    min_stack: Path, home: Path, monkeypatch: pytest.MonkeyPatch, lang: str, variant: str
) -> None:
    use(monkeypatch, home, "min-stack", lang, VARIANTS / f"{variant}.{EXT[lang]}")
    code, out = dsa("submit", "--lang", lang)
    want = EXPECTED[variant]
    assert LABELS[want] in out.splitlines()[0], out
    assert code == (0 if want == "AC" else 1)
    if variant == "wrong":
        # names the call, not just the whole result list
        assert "call #7 getMin() returned -3, expected -2" in out
    if variant == "crash":
        assert "top() is broken" in out and "call #" in out and " top" in out


def test_run_with_a_custom_call_sequence(
    min_stack: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, home, "min-stack", "python", VARIANTS / "correct.py")
    seq = '["MinStack","push","push","getMin","pop","getMin"]\n[[],[5],[2],[],[],[]]'
    code, out = dsa("run", "--case", seq)
    assert code == 0, out
    assert "[null,null,null,2,null,5]" in out
    code, out = dsa("run", "--case", '["MinStack","pop"]')
    assert code == 2 and "takes 2 lines" in out
    code, out = dsa("run", "--case", '["MinStack","peek"]\n[[],[]]')
    assert code == 2 and "unknown method" in out
    code, out = dsa("run", "--case", '["MinStack","push"]\n[[],["x"]]')
    assert code == 2 and "expected an integer" in out


def test_show_and_stubs(min_stack: Path, home: Path) -> None:
    code, out = dsa("show", "min-stack")
    assert code == 0 and "ref" in out and "hidden" in out
    for lang, needle in (
        ("cpp", "int getMin()"),
        ("java", "public int getMin()"),
        ("python", "def getMin(self) -> int:"),
    ):
        dsa("open", "min-stack", "--lang", lang, "--no-editor")
        text = (home / "work" / "min-stack" / "part1" / get_language(lang).sol_filename).read_text()
        assert (
            needle in text
            and "MinStack" in text
            and "Solution" not in text.replace("Solution.java", "")
        )


def test_a_wrong_ref_is_caught_by_stress_with_a_short_call_list(home: Path) -> None:
    shutil.copytree(LRU, home / "problems" / "lru-cache")
    wrong = home / "problems" / "lru-cache" / "part1" / "ref" / "solution.py"
    # re-putting an existing key forgets to refresh its recency; the sample never does that
    wrong.write_text(
        wrong.read_text().replace(
            "        if key in self.d:\n            self.d.move_to_end(key)\n",
            "        if key in self.d:\n            pass\n",
        )
    )
    yaml = home / "problems" / "lru-cache" / "problem.yaml"
    text = yaml.read_text()
    yaml.write_text(
        text.replace(
            "      - {lang: cpp, file: part1/ref/ref.cpp, origin: user, verified: true}\n", ""
        )
    )
    code, out = dsa("build", "lru-cache")
    assert code == 1, out
    assert "stress: ref and brute disagree" in out
    line = next(ln for ln in out.splitlines() if ln.strip().startswith("input:"))
    calls = line.count("(") - 1
    assert 3 <= calls <= 8, line  # LRUCache(c), put, put, get, put, get ...
    assert "LRUCache(" in line and "get(" in line


def test_lru_builds_and_accepts_its_own_refs(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shutil.copytree(LRU, home / "problems" / "lru-cache")
    code, out = dsa("build", "lru-cache")
    assert code == 0, out
    assert "oracle ref" in out
    for lang, name in (("cpp", "ref.cpp"), ("python", "solution.py")):
        use(monkeypatch, home, "lru-cache", lang, LRU / "part1" / "ref" / name)
        code, out = dsa("submit", "--lang", lang)
        assert code == 0 and "Accepted" in out, out
