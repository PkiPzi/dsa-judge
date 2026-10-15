"""Validator derived from the constraints (PLAN.md §6.3).

Checks every generated input *and every sample*. A sample that fails almost always means a
transcription error (OCR reading ``10^5`` as ``105``), so the build stops and names the
constraint.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any

from dsajudge.gen import trees
from dsajudge.model.constraints import Bound, ParamSpec
from dsajudge.model.expr import ExprError
from dsajudge.model.types import check_value


def _check_len(name: str, key: str, n: int, bound: Bound, env: dict[str, Any]) -> str | None:
    lo, hi = bound.eval_int(env)
    if not lo <= n <= hi:
        return f"{name}: {key} {n} is outside [{lo}, {hi}] (constraint {key}: {bound})"
    return None


def _check_chars(name: str, s: str, charset: str) -> str | None:
    bad = sorted(set(s) - set(charset))
    if bad:
        shown = "".join(bad[:5])
        return f"{name}: character(s) {shown!r} are not in the charset"
    return None


def _check_numbers(p: ParamSpec, vals: list[Any], env: dict[str, Any]) -> str | None:
    if not vals:
        return None
    if p.perm is not None:
        n = len(vals)
        if sorted(vals) != list(range(p.perm, p.perm + n)):
            return f"{p.name}: not a permutation of {p.perm}..{p.perm + n - 1}"
        return None
    assert p.each is not None
    lo, hi = p.each.eval(env) if p.t.base == "double" else p.each.eval_int(env)
    mn, mx = min(vals), max(vals)
    if mn < lo or mx > hi:
        bad = mn if mn < lo else mx
        return f"{p.name}: element {bad} is outside [{lo}, {hi}] (constraint each: {p.each})"
    if p.distinct and len(set(vals)) != len(vals):
        return f"{p.name}: elements are not distinct"
    if p.sorted:
        pairs = list(pairwise(vals))
        ok = {
            "asc": all(a < b for a, b in pairs),
            "desc": all(a > b for a, b in pairs),
            "nondec": all(a <= b for a, b in pairs),
            "noninc": all(a >= b for a, b in pairs),
        }[p.sorted]
        if not ok:
            return f"{p.name}: not sorted ({p.sorted})"
    return None


def _check_tree(p: ParamSpec, arr: list[Any], env: dict[str, Any]) -> str | None:
    assert p.length and p.each
    try:
        tree = trees.decode(arr)
    except ValueError as e:
        return f"{p.name}: invalid level-order tree: {e}"
    err = _check_len(p.name, "nodes", tree.n, p.length, env)
    if err:
        return err
    err = _check_numbers(p, tree.values, env)
    if err:
        return err
    if p.shape == "bst" and not trees.is_bst(tree):
        return f"{p.name}: not a binary search tree (strictly increasing in-order)"
    if p.shape == "line" and not trees.is_line(tree):
        return f"{p.name}: not a line (a node has two children)"
    if p.shape == "complete" and not trees.is_complete(arr):
        return f"{p.name}: not a complete tree"
    if p.shape == "balanced" and not trees.is_balanced(tree):
        return f"{p.name}: not height-balanced"
    return None


class _DSU:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.p[ra] = rb
        return True


def _check_graph(p: ParamSpec, edges: list[Any], env: dict[str, Any]) -> str | None:
    g = p.graph
    assert g
    n_lo, n_hi = g.n.eval_int(env)
    # n is usually a parameter; when it is a range we can't know it, so take the bound.
    n = n_hi
    width = 3 if g.weighted else 2
    for i, e in enumerate(edges):
        if len(e) != width:
            return f"{p.name}[{i}]: expected {width} values, got {len(e)}"
    if n_lo != n_hi:
        n = max([n_lo] + [max(e[0], e[1]) - g.base + 1 for e in edges])
    m = len(edges)
    if g.kind == "tree":
        if m != max(n - 1, 0):
            return f"{p.name}: a tree on {n} nodes has {max(n - 1, 0)} edges, got {m}"
    else:
        lo, hi = g.m.eval_int(env) if g.m else (0, m)
        if not lo <= m <= hi:
            return f"{p.name}: {m} edges is outside [{lo}, {hi}] (constraint graph.m)"
        if g.kind == "connected" and n >= 1 and m < n - 1:
            return f"{p.name}: {m} edges cannot connect {n} nodes"
    seen: set[tuple[int, int]] = set()
    dsu = _DSU(n)
    comps = n
    for i, e in enumerate(edges):
        u, v = e[0] - g.base, e[1] - g.base
        if not (0 <= u < n and 0 <= v < n):
            bad = e[0] if not 0 <= u < n else e[1]
            return f"{p.name}[{i}]: node {bad} is outside {g.base}..{g.base + n - 1}"
        if u == v:
            return f"{p.name}[{i}]: self loop on node {e[0]}"
        k = (u, v) if g.directed else (min(u, v), max(u, v))
        if k in seen:
            return f"{p.name}[{i}]: duplicate edge {e[0]}-{e[1]}"
        seen.add(k)
        if dsu.union(u, v):
            comps -= 1
        if g.weighted:
            wlo, whi = g.weighted.eval_int(env)
            if not wlo <= e[2] <= whi:
                return f"{p.name}[{i}]: weight {e[2]} is outside [{wlo}, {whi}]"
    if g.kind in ("tree", "connected") and n >= 1 and comps != 1:
        return f"{p.name}: graph is not connected ({comps} components)"
    if g.kind == "dag" and _has_cycle(n, edges, g.base):
        return f"{p.name}: graph has a cycle"
    return None


def _has_cycle(n: int, edges: list[Any], base: int) -> bool:
    indeg = [0] * n
    adj: list[list[int]] = [[] for _ in range(n)]
    for e in edges:
        u, v = e[0] - base, e[1] - base
        adj[u].append(v)
        indeg[v] += 1
    stack = [i for i in range(n) if indeg[i] == 0]
    seen = 0
    while stack:
        u = stack.pop()
        seen += 1
        for v in adj[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                stack.append(v)
    return seen != n


def _check_param(p: ParamSpec, v: Any, env: dict[str, Any]) -> str | None:
    t = p.t
    if t.kind == "scalar":
        if t.base in ("int", "long", "double"):
            assert p.range
            lo, hi = p.range.eval(env) if t.base == "double" else p.range.eval_int(env)
            if not lo <= v <= hi:
                return f"{p.name}: value {v} is outside [{lo}, {hi}] (constraint range: {p.range})"
            return None
        if t.base == "char":
            assert p.charset
            return _check_chars(p.name, v, p.charset)
        if t.base == "string":
            assert p.length and p.charset
            return _check_len(p.name, "len", len(v), p.length, env) or _check_chars(
                p.name, v, p.charset
            )
        return None
    if t.kind == "listnode":
        assert p.length
        return _check_len(p.name, "len", len(v), p.length, env) or _check_numbers(p, v, env)
    if t.kind == "treenode":
        return _check_tree(p, v, env)
    if t.depth == 2:
        if p.graph:
            return _check_graph(p, v, env)
        assert p.length and p.cols
        err = _check_len(p.name, "rows", len(v), p.length, env)
        if err:
            return err
        if v and not p.ragged and len({len(r) for r in v}) != 1:
            return f"{p.name}: rows have different lengths (add ragged: true if intended)"
        for r, row in enumerate(v):
            err = _check_len(f"{p.name}[{r}]", "cols", len(row), p.cols, env)
            if err:
                return err
        flat = [x for row in v for x in row]
        if t.base in ("int", "long", "double"):
            return _check_numbers(p, flat, env)
        if t.base == "char":
            assert p.charset
            return _check_chars(p.name, "".join(flat), p.charset)
        return None
    # 1-D array
    assert p.length
    err = _check_len(p.name, "len", len(v), p.length, env)
    if err:
        return err
    if t.base in ("int", "long", "double"):
        return _check_numbers(p, v, env)
    if t.base == "char":
        assert p.charset
        return _check_chars(p.name, "".join(v), p.charset)
    if t.base == "string":
        assert p.each_len and p.charset
        for i, s in enumerate(v):
            err = _check_len(f"{p.name}[{i}]", "each_len", len(s), p.each_len, env)
            if err:
                return err
            err = _check_chars(f"{p.name}[{i}]", s, p.charset)
            if err:
                return err
    return None


def validate_args(specs: list[ParamSpec], args: list[Any]) -> list[str]:
    """Constraint violations of one input; an empty list means valid."""
    if len(args) != len(specs):
        return [f"expected {len(specs)} argument(s), got {len(args)}"]
    errors: list[str] = []
    env: dict[str, Any] = {}
    for p, v in zip(specs, args, strict=True):
        err = check_value(p.t, v, p.name)
        if err:
            errors.append(err)
            return errors  # later bounds may refer to this value
        try:
            err = _check_param(p, v, env)
        except ExprError as e:
            err = f"{p.name}: {e}"
        if err:
            errors.append(err)
        env[p.name] = v
    return errors
