"""Constraint DSL: expressions, parsing, generation and validation (no toolchains needed)."""

from __future__ import annotations

from typing import Any

import pytest

from dsajudge.config import PlanConfig
from dsajudge.gen.generate import DslGenerator
from dsajudge.gen.plan import make_plan, stress_plan
from dsajudge.gen.rng import Rng, seed_for
from dsajudge.gen.validator import validate_args
from dsajudge.gen.values import GenError
from dsajudge.model.constraints import ConstraintError, parse_constraints
from dsajudge.model.expr import ExprError, parse_expr
from dsajudge.model.schema import Part

PLAN = PlanConfig()


def part_of(
    params: list[tuple[str, str]], constraints: dict[str, Any], returns: str = "int"
) -> Part:
    return Part.model_validate(
        {
            "id": 1,
            "title": "t",
            "statement": "s.md",
            "signature": {
                "method": "f",
                "params": [{"name": n, "type": t} for n, t in params],
                "returns": returns,
            },
            "constraints": constraints,
        }
    )


# ---------------------------------------------------------------- expressions


@pytest.mark.parametrize(
    ("src", "env", "want"),
    [
        ("1e5", {}, 100_000),
        (1e5, {}, 100_000),
        ("10^5", {}, 100_000),
        ("2*10^5", {}, 200_000),
        ("10^9 + 7", {}, 1_000_000_007),
        ("-10^4", {}, -10_000),
        ("2^31 - 1", {}, 2**31 - 1),
        ("1e18", {}, 10**18),
        ("10^18", {}, 10**18),
        ("len(nums) - 1", {"nums": [1, 2, 3]}, 2),
        ("max(1, len(s)//2)", {"s": "abcdef"}, 3),
        ("min(a)", {"a": [4, 2, 9]}, 2),
        ("sum(a)", {"a": [4, 2, 9]}, 15),
        ("abs(-3)", {}, 3),
        ("n/2", {"n": 8}, 4),
        ("−10^4", {}, -10_000),  # unicode minus from OCR
        ("2 × 10^5", {}, 200_000),
        ("1_000", {}, 1000),
    ],
)
def test_expr_eval(src: Any, env: dict[str, Any], want: int) -> None:
    value = parse_expr(src).eval(env)
    assert value == want
    assert isinstance(value, int)  # integer arithmetic stays exact


def test_expr_names_and_float_bounds() -> None:
    assert parse_expr("len(nums) + k").names() == {"nums", "k"}
    assert parse_expr("n/2").eval_int({"n": 7}) == 3
    assert parse_expr("n/2").eval_int({"n": 7}, round_up=True) == 4
    assert parse_expr("-n/2").eval_int({"n": 7}) == -4
    assert parse_expr(0.5).eval() == 0.5


@pytest.mark.parametrize(
    "bad", ["", "1 +", "foo(3)", "len", "2 $ 3", "(1", "1)", "10^1000", "1/0", True, None]
)
def test_expr_errors(bad: Any) -> None:
    with pytest.raises(ExprError):
        parse_expr(bad).eval({})


def test_expr_unknown_name_and_types() -> None:
    with pytest.raises(ExprError, match="unknown name"):
        parse_expr("x + 1").eval({})
    with pytest.raises(ExprError):
        parse_expr("len(3)").eval({})


def test_rng_is_stable_across_versions() -> None:
    # Pinned values: these must never change, or stored seeds stop reproducing their tests.
    r = Rng(12345)
    assert [r.randint(0, 99) for _ in range(8)] == [53, 93, 1, 38, 47, 24, 34, 72]
    items = list(range(10))
    Rng(99).shuffle(items)
    assert items == [7, 2, 0, 8, 5, 1, 4, 3, 9, 6]
    assert Rng(7).sample_range(5, 9, 5) == [7, 5, 8, 6, 9]
    assert seed_for("a", 1, "edge", 1) == 11881797535594944035
    assert seed_for("a", 1, "edge", 1) != seed_for("a", 1, "edge", 2)
    assert sorted(Rng(7).sample_range(5, 9, 5)) == [5, 6, 7, 8, 9]
    big = Rng(1).sample_range(0, 10**18, 50)
    assert len(set(big)) == 50
    with pytest.raises(ValueError):
        Rng(1).sample_range(0, 3, 5)


# ---------------------------------------------------------------- parsing


def test_parse_fixture_style_constraints() -> None:
    p = part_of(
        [("nums", "int[]"), ("k", "int")],
        {"nums": {"len": [1, "1e5"], "each": [-10000, "1e4"]}, "k": {"range": [1, "len(nums)"]}},
    )
    specs = parse_constraints(p)
    assert [s.name for s in specs] == ["nums", "k"]
    assert specs[0].length is not None and specs[0].length.eval_int({}) == (1, 100_000)
    assert specs[1].range is not None and specs[1].range.eval_int({"nums": [0] * 7}) == (1, 7)


@pytest.mark.parametrize(
    ("params", "constraints", "match"),
    [
        ([("a", "int[]")], {"a": {"lenn": [1, 5], "each": [0, 1]}}, "unknown key 'lenn'"),
        ([("a", "int[]")], {"a": {"len": [1, 5]}}, "missing 'each'"),
        ([("a", "int[]")], {}, "no constraint"),
        ([("a", "int[]")], {"b": {"len": [1, 5]}}, "not a parameter"),
        (
            [("k", "int"), ("a", "int[]")],
            {"k": {"range": [1, "len(a)"]}, "a": {"len": [1, 5], "each": [0, 1]}},
            "earlier parameters only",
        ),
        ([("k", "int")], {"k": {"range": [1, "k"]}}, "itself"),
        ([("k", "int")], {"k": {"range": [1]}}, "must be \\[lo, hi\\]"),
        ([("k", "int")], {"k": {"range": [1, "1e5 +"]}}, "range"),
        ([("s", "string")], {"s": {"len": [1, 5]}}, "missing 'charset'"),
        ([("a", "int[]")], {"a": {"len": [1, 5], "perm": 2}}, "perm must be 0 or 1"),
        ([("a", "double[]")], {"a": {"len": [1, 5], "each": [0, 1], "perm": 1}}, "perm only"),
        ([("a", "int[]")], {"a": {"len": [1, 5], "each": [0, 1], "sorted": "up"}}, "sorted must"),
        ([("t", "TreeNode")], {"t": {"nodes": [0, 5], "each": [0, 1], "shape": "zig"}}, "shape"),
        ([("e", "int[][]")], {"e": {"graph": {"kind": "tree"}}}, "missing 'n'"),
        ([("g", "string[][]")], {"g": {"rows": [1, 2], "cols": [1, 2]}}, "not supported"),
    ],
)
def test_constraint_errors(
    params: list[tuple[str, str]], constraints: dict[str, Any], match: str
) -> None:
    with pytest.raises(ConstraintError, match=match):
        parse_constraints(part_of(params, constraints))


def test_size_params_are_detected() -> None:
    p = part_of(
        [("n", "int"), ("k", "int"), ("a", "int[]")],
        {
            "n": {"range": [1, "1e5"]},
            "k": {"range": [0, 100]},
            "a": {"len": ["n", "n"], "each": [0, 9]},
        },
    )
    specs = parse_constraints(p)
    assert [s.is_size for s in specs] == [True, False, False]


# ---------------------------------------------------------------- generate + validate

CASES: dict[str, tuple[list[tuple[str, str]], dict[str, Any]]] = {
    "array": ([("nums", "int[]")], {"nums": {"len": [1, 1000], "each": [-10000, 10000]}}),
    "array-signs": ([("a", "int[]")], {"a": {"len": [0, 50], "each": [-5, 5]}}),
    "distinct-sorted": (
        [("a", "int[]")],
        {"a": {"len": [1, 60], "each": [-100, 100], "distinct": True, "sorted": "asc"}},
    ),
    "distinct-tight": (
        [("a", "int[]")],
        {"a": {"len": [1, 50], "each": [1, 12], "distinct": True}},
    ),
    "nondec": ([("a", "int[]")], {"a": {"len": [1, 60], "each": [0, 9], "sorted": "nondec"}}),
    "noninc": ([("a", "int[]")], {"a": {"len": [1, 60], "each": [0, 9], "sorted": "noninc"}}),
    "perm": ([("p", "int[]")], {"p": {"len": [1, 100], "perm": 1}}),
    "long": ([("a", "long[]")], {"a": {"len": [1, 50], "each": [-(10**18), "10^18"]}}),
    "dependent": (
        [("nums", "int[]"), ("k", "int")],
        {"nums": {"len": [1, 200], "each": [0, 9]}, "k": {"range": [1, "len(nums)"]}},
    ),
    "size-param": (
        [("n", "int"), ("a", "int[]"), ("b", "int[]")],
        {
            "n": {"range": [1, 500]},
            "a": {"len": ["n", "n"], "each": [0, 9]},
            "b": {"len": [0, "n"], "each": [0, 9]},
        },
    ),
    "string": ([("s", "string")], {"s": {"len": [1, 300], "charset": "lowercase"}}),
    "string-lit": ([("s", "string")], {"s": {"len": [0, 40], "charset": "ab#"}}),
    "digits": ([("s", "string")], {"s": {"len": [1, 40], "charset": "digits"}}),
    "string-array": (
        [("w", "string[]")],
        {"w": {"len": [1, 30], "each_len": [1, 6], "charset": "lowercase"}},
    ),
    "double": ([("x", "double")], {"x": {"range": [-1.5, 1e3], "decimals": 3}}),
    "double-array": ([("x", "double[]")], {"x": {"len": [1, 30], "each": [0, 1]}}),
    "bool": ([("b", "bool"), ("c", "bool[]")], {"c": {"len": [0, 9]}}),
    "char": ([("c", "char")], {"c": {"charset": "xyz"}}),
    "char-array": ([("c", "char[]")], {"c": {"len": [1, 20], "charset": "binary"}}),
    "grid": ([("g", "char[][]")], {"g": {"rows": [1, 300], "cols": [1, 300], "each": "01"}}),
    "matrix": ([("m", "int[][]")], {"m": {"rows": [1, 40], "cols": [1, 40], "each": [-9, 9]}}),
    "ragged": (
        [("m", "int[][]")],
        {"m": {"rows": [1, 20], "cols": [1, 9], "each": [0, 9], "ragged": True}},
    ),
    "bool-matrix": ([("m", "bool[][]")], {"m": {"rows": [1, 9], "cols": [1, 9]}}),
    "list": ([("h", "ListNode")], {"h": {"len": [0, 200], "each": [-100, 100]}}),
    "list-sorted": (
        [("h", "ListNode")],
        {"h": {"nodes": [1, 200], "each": [-100, 100], "sorted": "nondec"}},
    ),
    "tree-any": ([("t", "TreeNode")], {"t": {"nodes": [0, 500], "each": [-1000, 1000]}}),
    "tree-bst": (
        [("t", "TreeNode")],
        {"t": {"nodes": [1, 300], "each": [-1000, 1000], "shape": "bst"}},
    ),
    "tree-balanced": (
        [("t", "TreeNode")],
        {"t": {"nodes": [1, 300], "each": [0, 9], "shape": "balanced"}},
    ),
    "tree-line": ([("t", "TreeNode")], {"t": {"nodes": [1, 300], "each": [0, 9], "shape": "line"}}),
    "tree-complete": (
        [("t", "TreeNode")],
        {"t": {"nodes": [1, 300], "each": [0, 9], "shape": "complete"}},
    ),
    "graph-tree": (
        [("n", "int"), ("edges", "int[][]")],
        {
            "n": {"range": [1, 300]},
            "edges": {"graph": {"n": "n", "kind": "tree", "weighted": [1, "1e9"]}},
        },
    ),
    "graph-connected": (
        [("n", "int"), ("edges", "int[][]")],
        {"n": {"range": [1, 200]}, "edges": {"graph": {"n": "n", "kind": "connected", "base": 1}}},
    ),
    "graph-connected-m": (
        [("n", "int"), ("edges", "int[][]")],
        {
            "n": {"range": [2, 200]},
            "edges": {"graph": {"n": "n", "kind": "connected", "m": ["n - 1", "n + 5"]}},
        },
    ),
    "graph-dag": (
        [("n", "int"), ("edges", "int[][]")],
        {
            "n": {"range": [1, 200]},
            "edges": {"graph": {"n": "n", "kind": "dag", "weighted": [-5, 5]}},
        },
    ),
    "graph-any-directed": (
        [("n", "int"), ("edges", "int[][]")],
        {
            "n": {"range": [1, 100]},
            "edges": {"graph": {"n": "n", "kind": "any", "directed": True}},
        },
    ),
    "graph-any": (
        [("n", "int"), ("edges", "int[][]")],
        {"n": {"range": [0, 100]}, "edges": {"graph": {"n": "n", "kind": "any"}}},
    ),
}


def all_specs(plan: PlanConfig = PLAN) -> list[Any]:
    return make_plan("slug", 1, plan) + stress_plan("slug", 1, 12)


@pytest.mark.parametrize("name", sorted(CASES))
def test_generated_inputs_validate(name: str) -> None:
    params, constraints = CASES[name]
    specs = parse_constraints(part_of(params, constraints))
    gen = DslGenerator(specs, PLAN)
    seen = 0
    for t in all_specs():
        if t.size == "max" and name in ("grid",):
            pass  # 300x300 is fine; keep it
        args = gen.generate(t)
        errs = validate_args(specs, args)
        assert not errs, f"{name} {t.id} ({t.size}/{t.flavor}): {errs[0]}"
        seen += 1
    assert seen > 60


@pytest.mark.parametrize("name", ["array", "tree-bst", "graph-connected", "grid", "string"])
def test_generation_is_deterministic(name: str) -> None:
    params, constraints = CASES[name]
    specs = parse_constraints(part_of(params, constraints))
    a, b = DslGenerator(specs, PLAN), DslGenerator(specs, PLAN)
    for t in make_plan("slug", 1, PLAN):
        assert a.generate(t) == b.generate(t)


def test_max_group_hits_maximum_size_and_structure() -> None:
    specs = parse_constraints(
        part_of([("nums", "int[]")], {"nums": {"len": [1, "1e5"], "each": [-10000, 10000]}})
    )
    gen = DslGenerator(specs, PLAN)
    mx = [t for t in make_plan("s", 1, PLAN) if t.group == "max"]
    assert len(mx) == 6
    outs = {t.flavor: gen.generate(t)[0] for t in mx}
    assert all(len(v) == 100_000 for v in outs.values())
    assert outs["asc"] == sorted(outs["asc"])
    assert outs["desc"] == sorted(outs["desc"], reverse=True)
    assert len(set(outs["equal"])) == 1
    alt = outs["alternating"]
    assert all((alt[i] > 0) != (alt[i + 1] > 0) for i in range(0, 1000))
    assert set(outs["extremes"]) == {-10000, 10000}


def test_edge_group_covers_minimums_and_extremes() -> None:
    specs = parse_constraints(
        part_of([("nums", "int[]")], {"nums": {"len": [1, "1e5"], "each": [-10000, 10000]}})
    )
    gen = DslGenerator(specs, PLAN)
    edge = {tuple(gen.generate(t)[0]) for t in make_plan("s", 1, PLAN) if t.group == "edge"}
    assert (-10000,) in edge and (10000,) in edge and (0,) in edge
    assert any(len(v) == 2 for v in edge)
    assert min(len(v) for v in edge) == 1


def test_special_shapes_in_max() -> None:
    specs = parse_constraints(
        part_of([("t", "TreeNode")], {"t": {"nodes": [1, "1e5"], "each": [0, 9]}})
    )
    gen = DslGenerator(specs, PLAN)
    by = {t.flavor: gen.generate(t)[0] for t in make_plan("s", 1, PLAN) if t.group == "max"}
    # asc -> right-leaning line tree: null, value, null, value ...
    line = by["asc"]
    assert len(line) == 2 * 100_000 - 1
    assert all(line[i] is None for i in range(1, len(line), 2))
    assert len(by["random"]) >= 100_000


def test_graph_structural_cases() -> None:
    specs = parse_constraints(
        part_of(
            [("n", "int"), ("e", "int[][]")],
            {"n": {"range": [1, 1000]}, "e": {"graph": {"n": "n", "kind": "tree"}}},
        )
    )
    gen = DslGenerator(specs, PLAN)
    by = {t.flavor: gen.generate(t) for t in make_plan("s", 1, PLAN) if t.group == "max"}
    n, path = by["asc"]
    assert len(path) == n - 1
    assert all(abs(a - b) == 1 for a, b in path)
    n2, star = by["equal"]
    degree: dict[int, int] = {}
    for a, b in star:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
    assert max(degree.values()) == n2 - 1


def test_dense_graphs_do_not_hang() -> None:
    specs = parse_constraints(
        part_of(
            [("n", "int"), ("e", "int[][]")],
            {
                "n": {"range": [5, 5]},
                "e": {"graph": {"n": "n", "kind": "any", "m": [10, 10]}},  # complete graph
            },
        )
    )
    gen = DslGenerator(specs, PLAN)
    for t in make_plan("s", 1, PLAN)[:10]:
        _n, edges = gen.generate(t)
        assert len({tuple(sorted(e)) for e in edges}) == 10


def test_generator_errors_are_reported() -> None:
    specs = parse_constraints(
        part_of(
            [("a", "int[]"), ("k", "int")],
            {"a": {"len": [0, 5], "each": [0, 9]}, "k": {"range": [1, "len(a)"]}},
        )
    )
    gen = DslGenerator(specs, PLAN)
    t = next(t for t in make_plan("s", 1, PLAN) if t.size == "min")  # len 0 -> k range [1, 0]
    with pytest.raises(GenError, match="empty range"):
        gen.generate(t)


# ---------------------------------------------------------------- validator rejects


@pytest.mark.parametrize(
    ("name", "args", "match"),
    [
        ("array", [[]], "len 0 is outside"),
        ("array", [[1] * 1001], "len 1001"),
        ("array", [[20000]], "element 20000"),
        ("array", ["x"], "expected an array"),
        ("distinct-sorted", [[1, 1]], "distinct|sorted"),
        ("distinct-sorted", [[3, 2]], "not sorted"),
        ("nondec", [[3, 2]], "not sorted"),
        ("perm", [[1, 3]], "not a permutation"),
        ("dependent", [[1, 2], 3], "value 3 is outside"),
        ("size-param", [3, [1, 2], []], "len 2 is outside"),
        ("string", ["ABC"], "not in the charset"),
        ("string", [""], "len 0"),
        ("char", ["q"], "not in the charset"),
        ("grid", [[["0", "1"], ["0"]]], "different lengths"),
        ("grid", [[["0", "2"]]], "not in the charset"),
        ("tree-bst", [[2, 3, 1]], "binary search tree"),
        ("tree-line", [[1, 2, 3]], "not a line"),
        ("tree-any", [[1, None, None, 5]], "invalid level-order"),
        ("graph-tree", [3, [[0, 1, 5]]], "has 2 edges"),
        ("graph-tree", [3, [[0, 1, 5], [0, 0, 5]]], "self loop"),
        ("graph-tree", [3, [[0, 1, 5], [1, 5, 5]]], "outside 0..2"),
        ("graph-tree", [3, [[0, 1, 5], [1, 0, 5]]], "duplicate edge"),
        ("graph-tree", [4, [[0, 1, 5], [0, 1, 6], [2, 3, 5]]], "duplicate|connected"),
        ("graph-tree", [3, [[0, 1, 0], [1, 2, 5]]], "weight 0"),
        ("graph-connected", [4, [[1, 2], [3, 4]]], "cannot connect|not connected"),
        ("graph-dag", [3, [[0, 1, 1], [1, 2, 1], [2, 0, 1]]], "cycle"),
    ],
)
def test_validator_rejects(name: str, args: list[Any], match: str) -> None:
    params, constraints = CASES[name]
    specs = parse_constraints(part_of(params, constraints))
    errs = validate_args(specs, args)
    assert errs, f"{args} should be invalid"
    import re

    assert re.search(match, errs[0]), errs


def test_validator_accepts_handwritten() -> None:
    params, constraints = CASES["dependent"]
    specs = parse_constraints(part_of(params, constraints))
    assert validate_args(specs, [[1, 2, 3], 2]) == []
    assert validate_args(specs, [[1, 2, 3]]) == ["expected 2 argument(s), got 1"]
