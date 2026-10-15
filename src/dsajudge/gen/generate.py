"""Generate inputs from the constraint DSL (no AI involved).

``DslGenerator.generate(spec)`` returns the argument list (canonical JSON values, in signature
order) for one TestSpec. The same seed always gives the same input.
"""

from __future__ import annotations

from typing import Any

from dsajudge.config import PlanConfig
from dsajudge.gen import trees
from dsajudge.gen.graphs import default_m_bounds, generate_graph
from dsajudge.gen.plan import TestSpec
from dsajudge.gen.rng import Rng
from dsajudge.gen.values import (
    GenError,
    char_scalar,
    check_total,
    float_scalar,
    float_values,
    int_scalar,
    int_values,
    permutation,
    pick_dim,
    sort_values,
    string_value,
)
from dsajudge.model.constraints import ParamSpec
from dsajudge.model.expr import ExprError

D1 = "d1"
D2 = "d2"


class DslGenerator:
    def __init__(self, specs: list[ParamSpec], plan: PlanConfig) -> None:
        self.specs = specs
        self.plan = plan

    def generate(self, test: TestSpec) -> list[Any]:
        rng = Rng(test.seed)
        env: dict[str, Any] = {}
        args: list[Any] = []
        for p in self.specs:
            try:
                v = self._param(rng, p, env, test)
            except ExprError as e:
                raise GenError(f"{p.name}: {e}") from e
            env[p.name] = v
            args.append(v)
        return args

    def value(self, rng: Rng, p: ParamSpec, env: dict[str, Any], test: TestSpec) -> Any:
        """One value for ``p`` (used by the ops generator for constructor and call arguments)."""
        try:
            return self._param(rng, p, env, test)
        except ExprError as e:
            raise GenError(f"{p.name}: {e}") from e

    # ------------------------------------------------------------ helpers

    def _caps(self, kind: str) -> tuple[int, int]:
        if kind == D2:
            return self.plan.small_cap_2d, self.plan.medium_cap_2d
        return self.plan.small_cap, self.plan.medium_cap

    def _dim(
        self, rng: Rng, p: ParamSpec, bound: Any, env: dict[str, Any], test: TestSpec, kind: str
    ) -> int:
        lo, hi = bound.eval_int(env)
        return pick_dim(rng, lo, hi, test.size, self._caps(kind), f"{p.name}: {bound}")

    def _range(self, p: ParamSpec, env: dict[str, Any]) -> tuple[int, int]:
        assert p.each is not None
        lo, hi = p.each.eval_int(env)
        if hi < lo:
            raise GenError(f"{p.name}: empty element range [{lo}, {hi}]")
        return lo, hi

    # ------------------------------------------------------------ per type

    def _param(self, rng: Rng, p: ParamSpec, env: dict[str, Any], test: TestSpec) -> Any:
        t, flavor = p.t, test.flavor
        if t.kind == "scalar":
            return self._scalar(rng, p, env, test)
        if t.kind == "listnode":
            return self._int_array(rng, p, env, test, flavor)
        if t.kind == "treenode":
            return self._tree(rng, p, env, test)
        if t.depth == 2:
            if p.graph:
                return self._graph(rng, p, env, test)
            return self._matrix(rng, p, env, test)
        return self._array(rng, p, env, test)

    def _scalar(self, rng: Rng, p: ParamSpec, env: dict[str, Any], test: TestSpec) -> Any:
        base, flavor = p.t.base, test.flavor
        if base == "bool":
            if flavor == "all_min":
                return False
            if flavor == "all_max":
                return True
            return rng.chance(0.5)
        if base == "char":
            assert p.charset
            return char_scalar(rng, p.charset, flavor)
        if base == "string":
            assert p.length and p.charset
            n = self._dim(rng, p, p.length, env, test, D1)
            return string_value(rng, n, p.charset, flavor, p.name)
        assert p.range
        if base == "double":
            lo, hi = p.range.eval(env)
            return float_scalar(rng, float(lo), float(hi), p.decimals, flavor, p.name)
        lo_i, hi_i = p.range.eval_int(env)
        if p.is_size:
            # A dimension other params depend on (n): scale it like an array length.
            return pick_dim(rng, lo_i, hi_i, test.size, self._caps(D1), p.name)
        return int_scalar(rng, lo_i, hi_i, flavor, p.name)

    def _array(self, rng: Rng, p: ParamSpec, env: dict[str, Any], test: TestSpec) -> Any:
        base = p.t.base
        if base in ("int", "long"):
            return self._int_array(rng, p, env, test, test.flavor)
        assert p.length
        n = self._dim(rng, p, p.length, env, test, D1)
        flavor = test.flavor
        if base == "double":
            assert p.each
            lo, hi = p.each.eval(env)
            return float_values(rng, n, float(lo), float(hi), 2, flavor, p.name)
        if base == "bool":
            if flavor == "all_min":
                return [False] * n
            if flavor == "all_max":
                return [True] * n
            if flavor == "alternating":
                return [i % 2 == 1 for i in range(n)]
            return [rng.chance(0.5) for _ in range(n)]
        assert p.charset
        if base == "char":
            return list(string_value(rng, n, p.charset, flavor, p.name))
        assert p.each_len  # string[]
        out: list[str] = []
        for _ in range(n):
            ln = self._dim(rng, p, p.each_len, env, test, D1)
            out.append(string_value(rng, ln, p.charset, flavor, p.name))
        return out

    def _int_array(
        self, rng: Rng, p: ParamSpec, env: dict[str, Any], test: TestSpec, flavor: str
    ) -> list[int]:
        assert p.length
        n = self._dim(rng, p, p.length, env, test, D1)
        if p.perm is not None:
            return permutation(rng, n, p.perm, flavor)
        lo, hi = self._range(p, env)
        distinct = p.distinct or p.sorted in ("asc", "desc")
        vals = int_values(rng, n, lo, hi, flavor, distinct=distinct, what=p.name)
        if p.sorted:
            vals = sort_values(vals, p.sorted)
        return vals

    def _matrix(self, rng: Rng, p: ParamSpec, env: dict[str, Any], test: TestSpec) -> Any:
        assert p.length and p.cols
        rows = self._dim(rng, p, p.length, env, test, D2)
        cols = self._dim(rng, p, p.cols, env, test, D2)
        check_total(rows * cols, p.name)
        flavor, base = test.flavor, p.t.base
        widths = (
            [self._dim(rng, p, p.cols, env, test, D2) for _ in range(rows)]
            if p.ragged
            else [cols] * rows
        )
        total = sum(widths)
        flat: list[Any]
        if base in ("int", "long"):
            lo, hi = self._range(p, env)
            flat = list(int_values(rng, total, lo, hi, flavor, what=p.name))
            if flavor == "alternating":  # checkerboard of the two extremes
                flat = []
                for r, w in enumerate(widths):
                    flat += [lo if (r + c) % 2 else hi for c in range(w)]
        elif base == "double":
            assert p.each
            flo, fhi = p.each.eval(env)
            flat = float_values(rng, total, float(flo), float(fhi), 2, flavor, p.name)
        elif base == "bool":
            flat = [rng.chance(0.5) for _ in range(total)]
            if flavor == "all_min":
                flat = [False] * total
            elif flavor == "all_max":
                flat = [True] * total
        else:
            assert p.charset
            flat = list(string_value(rng, total, p.charset, flavor, p.name))
            if flavor == "alternating" and len(p.charset) > 1:
                flat = []
                for r, w in enumerate(widths):
                    flat += [p.charset[(r + c) % 2] for c in range(w)]
        out: list[list[Any]] = []
        i = 0
        for w in widths:
            out.append(flat[i : i + w])
            i += w
        return out

    def _tree(self, rng: Rng, p: ParamSpec, env: dict[str, Any], test: TestSpec) -> Any:
        assert p.length
        n = self._dim(rng, p, p.length, env, test, D1)
        lo, hi = self._range(p, env)
        if p.distinct and hi - lo + 1 < n:
            n = hi - lo + 1
        tree = trees.generate_tree(rng, n, lo, hi, p.shape, test.flavor, p.distinct, p.name)
        return trees.encode(tree)

    def _graph(self, rng: Rng, p: ParamSpec, env: dict[str, Any], test: TestSpec) -> Any:
        g = p.graph
        assert g
        n = self._dim(rng, p, g.n, env, test, D1)
        if g.m is not None:
            m_lo, m_hi = g.m.eval_int(env)
        else:
            m_lo, m_hi = default_m_bounds(n, g.kind, g.directed)
        weights = g.weighted.eval_int(env) if g.weighted else None
        return generate_graph(
            rng,
            n,
            g.kind,
            g.directed,
            m_lo,
            m_hi,
            test.flavor,
            test.size,
            g.base,
            weights,
            p.name,
        )
