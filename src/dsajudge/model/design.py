"""Helpers for design problems (``mode: ops``).

Wire format, as on LeetCode: a test input has two lines, the operation names
(``["MinStack","push","getMin"]``, the class name first) and one argument list per operation
(``[[],[-2],[]]``). The expected output is one result per operation, ``null`` for the
constructor and for ``void`` methods.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from dsajudge.model.canon import canon_equal, dumps
from dsajudge.model.expr import parse_expr
from dsajudge.model.schema import Design, Param
from dsajudge.model.types import check_value


def _check_args(params: list[Param], args: Any, where: str) -> str | None:
    if not isinstance(args, list):
        return f"{where}: expected an argument list, got {dumps(args)[:40]}"
    if len(args) != len(params):
        return (
            f"{where}: takes {len(params)} argument(s) "
            f"({', '.join(p.name for p in params) or 'none'}), got {len(args)}"
        )
    for p, a in zip(params, args, strict=True):
        err = check_value(p.tnode, a, f"{where} {p.name}")
        if err:
            return err
    return None


def check_sequence(
    design: Design, ops: Any, args: Any, expected: Any = None, where: str = "input"
) -> str | None:
    """Type-check a call sequence (and, if given, its expected results); None if fine."""
    if not isinstance(ops, list) or not ops or not all(isinstance(o, str) for o in ops):
        return f"{where}: ops must be a non-empty list of names"
    if not isinstance(args, list) or len(args) != len(ops):
        n = len(args) if isinstance(args, list) else "?"
        return f"{where}: {len(ops)} operation(s) but {n} argument list(s)"
    if ops[0] != design.class_name:
        return f"{where}: the first operation must be the class name {design.class_name!r}"
    err = _check_args(design.constructor, args[0], f"{where} {design.class_name}")
    if err:
        return err
    names = {m.name for m in design.methods}
    for i in range(1, len(ops)):
        if ops[i] not in names:
            return f"{where}: call #{i}: unknown method {ops[i]!r} (have {sorted(names)})"
        m = design.method(ops[i])
        err = _check_args(m.params, args[i], f"{where} call #{i} {m.name}")
        if err:
            return err
    if expected is None:
        return None
    if not isinstance(expected, list) or len(expected) != len(ops):
        n = len(expected) if isinstance(expected, list) else "?"
        return f"{where}: expected has {n} result(s) for {len(ops)} operation(s)"
    if expected[0] is not None:
        return f"{where}: the constructor's result must be null"
    for i in range(1, len(ops)):
        m = design.method(ops[i])
        if m.ret.is_void:
            if expected[i] is not None:
                return f"{where}: call #{i} {m.name} returns void, expected null"
            continue
        err = check_value(m.ret, expected[i], f"{where} call #{i} {m.name} result")
        if err:
            return err
    return None


def initial_counters(design: Design, ctor_args: list[Any]) -> dict[str, int]:
    """Starting value of every counter, given the constructor's arguments."""
    env = {p.name: a for p, a in zip(design.constructor, ctor_args, strict=True)}
    out: dict[str, int] = {}
    for name, init in design.counters.items():
        out[name] = init if isinstance(init, int) else parse_expr(init).eval_int(env)
    return out


def allowed(design: Design, counters: dict[str, int]) -> list[str]:
    """Methods whose ``requires`` hold in the current counter state."""
    return [
        m.name for m in design.methods if all(counters[c] >= need for c, need in m.requires.items())
    ]


def apply_effects(design: Design, counters: dict[str, int], method: str) -> None:
    for c, delta in design.method(method).effects.items():
        counters[c] += delta


def replay_counters(design: Design, ops: list[str], args: list[Any]) -> str | None:
    """Replay a sequence against the counters; the first call whose ``requires`` fails."""
    counters = initial_counters(design, args[0])
    for i in range(1, len(ops)):
        m = design.method(ops[i])
        for c, need in m.requires.items():
            if counters[c] < need:
                return f"call #{i} {m.name} needs {c} >= {need} but {c} is {counters[c]}"
        apply_effects(design, counters, m.name)
    return None


def describe_call(ops: list[str], args: list[Any], i: int) -> str:
    return f"call #{i} {ops[i]}({', '.join(dumps(a) for a in args[i])})"


def describe_difference(
    ops: list[str],
    args: list[Any],
    expected: list[Any],
    got: Any,
    equal: Callable[[Any, Any], bool] = canon_equal,
) -> str | None:
    """Which call first returned the wrong thing, or None if nothing differs."""
    if not isinstance(got, list):
        return "the output is not a list of results"
    for i, (e, g) in enumerate(zip(expected, got, strict=False)):
        if not equal(e, g):
            return f"{describe_call(ops, args, i)} returned {dumps(g)}, expected {dumps(e)}"
    if len(got) != len(expected):
        return f"{len(got)} result(s), expected {len(expected)}"
    return None
