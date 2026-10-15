"""Constraint DSL (PLAN.md §6.1): parse a part's ``constraints`` into typed specs.

Params are listed in signature order; a bound may reference earlier params (``len(nums)``).
Parsing is strict: unknown keys, missing keys and forward references are errors that name
the param, so a transcription slip is caught at build time rather than producing odd tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from dsajudge.model.expr import Expr, ExprError, Number, parse_expr
from dsajudge.model.schema import Part
from dsajudge.model.types import TypeNode

Shape = Literal["any", "bst", "balanced", "line", "complete"]
GraphKind = Literal["tree", "connected", "dag", "any"]
SortMode = Literal["asc", "desc", "nondec", "noninc"]

SHAPES = ("any", "bst", "balanced", "line", "complete")
GRAPH_KINDS = ("tree", "connected", "dag", "any")
SORT_MODES = ("asc", "desc", "nondec", "noninc")

CHARSETS = {
    "lowercase": "abcdefghijklmnopqrstuvwxyz",
    "uppercase": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "letters": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
    "digits": "0123456789",
    "alnum": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
    "binary": "01",
}


class ConstraintError(ValueError):
    """A constraint is malformed. ``param`` names the offending parameter."""

    def __init__(self, param: str, message: str) -> None:
        super().__init__(f"constraint {param!r}: {message}")
        self.param = param
        self.message = message


def resolve_charset(spec: str) -> str:
    """Named sets (lowercase, digits, ...) or a literal string of allowed characters."""
    chars = CHARSETS.get(spec)
    if chars is not None:
        return chars
    seen: dict[str, None] = {}
    for c in spec:
        seen.setdefault(c, None)
    out = "".join(seen)
    if not out:
        raise ValueError("empty charset")
    return out


@dataclass(frozen=True)
class Bound:
    lo: Expr
    hi: Expr

    def eval(self, env: dict[str, Any]) -> tuple[Number, Number]:
        return self.lo.eval(env), self.hi.eval(env)

    def eval_int(self, env: dict[str, Any]) -> tuple[int, int]:
        return self.lo.eval_int(env, round_up=True), self.hi.eval_int(env)

    def names(self) -> set[str]:
        return self.lo.names() | self.hi.names()

    def __str__(self) -> str:
        return f"[{self.lo}, {self.hi}]"


@dataclass(frozen=True)
class GraphSpec:
    n: Bound
    kind: GraphKind = "any"
    directed: bool = False
    weighted: Bound | None = None
    m: Bound | None = None
    base: int = 0

    def exprs(self) -> list[Bound]:
        return [b for b in (self.n, self.weighted, self.m) if b is not None]


@dataclass(frozen=True)
class ParamSpec:
    name: str
    t: TypeNode
    range: Bound | None = None  # scalar value range
    decimals: int = 2  # double
    length: Bound | None = None  # array length, string length, list/tree node count, matrix rows
    cols: Bound | None = None
    each: Bound | None = None  # numeric element range
    each_len: Bound | None = None  # string[] element length
    charset: str | None = None  # resolved characters
    distinct: bool = False
    sorted: SortMode | None = None
    perm: int | None = None
    ragged: bool = False
    shape: Shape = "any"
    graph: GraphSpec | None = None
    # Dimensions that other params use; scaled by test size rather than treated as values.
    is_size: bool = field(default=False, compare=False)

    def size_bounds(self) -> list[Bound]:
        """Bounds that define how big this param's value is."""
        out = [b for b in (self.length, self.cols, self.each_len) if b is not None]
        if self.graph:
            out += self.graph.exprs()
        return out

    def all_bounds(self) -> list[Bound]:
        out = self.size_bounds()
        if self.range:
            out.append(self.range)
        if self.each:
            out.append(self.each)
        return out


def _bound(param: str, key: str, raw: Any, *, allow_single: bool = False) -> Bound:
    try:
        if isinstance(raw, list | tuple):
            if len(raw) != 2:
                raise ConstraintError(param, f"{key} must be [lo, hi], got {list(raw)!r}")
            return Bound(parse_expr(raw[0]), parse_expr(raw[1]))
        if allow_single:
            e = parse_expr(raw)
            return Bound(e, e)
    except ExprError as e:
        raise ConstraintError(param, f"{key}: {e}") from e
    raise ConstraintError(param, f"{key} must be [lo, hi], got {raw!r}")


def _flag(param: str, key: str, raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    raise ConstraintError(param, f"{key} must be true or false, got {raw!r}")


def _charset(param: str, raw: Any) -> str:
    if not isinstance(raw, str):
        raise ConstraintError(param, f"charset must be a string, got {raw!r}")
    try:
        return resolve_charset(raw)
    except ValueError as e:
        raise ConstraintError(param, str(e)) from e


def _need(param: str, d: dict[str, Any], *keys: str) -> None:
    for k in keys:
        if k not in d:
            raise ConstraintError(param, f"missing {k!r}")


def _only(param: str, d: dict[str, Any], allowed: set[str], t: TypeNode) -> None:
    extra = sorted(set(d) - allowed)
    if extra:
        names = ", ".join(sorted(allowed)) or "none"
        raise ConstraintError(param, f"unknown key {extra[0]!r} for type {t} (allowed: {names})")


def _charset_key(param: str, d: dict[str, Any]) -> str | None:
    """``charset: lowercase`` or, for grids, ``each: "01"``."""
    if "charset" in d and "each" in d:
        raise ConstraintError(param, "give charset or each, not both")
    raw = d.get("charset", d.get("each"))
    return None if raw is None else _charset(param, raw)


def _graph(param: str, raw: Any) -> GraphSpec:
    if not isinstance(raw, dict):
        raise ConstraintError(param, "graph must be a mapping")
    allowed = {"n", "kind", "directed", "weighted", "m", "base"}
    extra = sorted(set(raw) - allowed)
    if extra:
        raise ConstraintError(
            param, f"graph: unknown key {extra[0]!r} (allowed: {', '.join(sorted(allowed))})"
        )
    _need(param, raw, "n")
    kind = raw.get("kind", "any")
    if kind not in GRAPH_KINDS:
        raise ConstraintError(param, f"graph.kind must be one of {', '.join(GRAPH_KINDS)}")
    directed = _flag(param, "graph.directed", raw["directed"]) if "directed" in raw else False
    if kind == "dag":
        directed = True
    base = raw.get("base", 0)
    if base not in (0, 1) or isinstance(base, bool):
        raise ConstraintError(param, "graph.base must be 0 or 1 (first node label)")
    weighted = _bound(param, "graph.weighted", raw["weighted"]) if "weighted" in raw else None
    m = _bound(param, "graph.m", raw["m"], allow_single=True) if "m" in raw else None
    if kind == "tree" and m is not None:
        raise ConstraintError(param, "a tree has exactly n-1 edges; remove graph.m")
    return GraphSpec(
        n=_bound(param, "graph.n", raw["n"], allow_single=True),
        kind=kind,
        directed=directed,
        weighted=weighted,
        m=m,
        base=int(base),
    )


def _parse_param(name: str, t: TypeNode, d: dict[str, Any]) -> ParamSpec:
    integral = t.base in ("int", "long") if t.kind in ("scalar", "array") else False

    if t.kind == "scalar":
        if t.base in ("int", "long"):
            _only(name, d, {"range"}, t)
            _need(name, d, "range")
            return ParamSpec(name, t, range=_bound(name, "range", d["range"]))
        if t.base == "double":
            _only(name, d, {"range", "decimals"}, t)
            _need(name, d, "range")
            dec = d.get("decimals", 2)
            if not isinstance(dec, int) or isinstance(dec, bool) or not 0 <= dec <= 9:
                raise ConstraintError(name, "decimals must be an integer 0..9")
            return ParamSpec(name, t, range=_bound(name, "range", d["range"]), decimals=dec)
        if t.base == "bool":
            _only(name, d, set(), t)
            return ParamSpec(name, t)
        if t.base == "char":
            _only(name, d, {"charset", "each"}, t)
            cs = _charset_key(name, d)
            if cs is None:
                raise ConstraintError(name, "missing 'charset'")
            return ParamSpec(name, t, charset=cs)
        _only(name, d, {"len", "charset", "each"}, t)  # string
        _need(name, d, "len")
        cs = _charset_key(name, d)
        if cs is None:
            raise ConstraintError(name, "missing 'charset' (e.g. lowercase, digits, or \"abc\")")
        return ParamSpec(
            name, t, length=_bound(name, "len", d["len"], allow_single=True), charset=cs
        )

    if t.kind == "listnode":
        _only(name, d, {"len", "nodes", "each", "distinct", "sorted"}, t)
        key = "len" if "len" in d else "nodes"
        if "len" in d and "nodes" in d:
            raise ConstraintError(name, "give len or nodes, not both")
        _need(name, d, key, "each")
        return ParamSpec(
            name,
            t,
            length=_bound(name, key, d[key], allow_single=True),
            each=_bound(name, "each", d["each"]),
            distinct=_flag(name, "distinct", d.get("distinct", False)),
            sorted=_sorted(name, d),
        )

    if t.kind == "treenode":
        _only(name, d, {"nodes", "len", "each", "shape", "distinct"}, t)
        if "len" in d and "nodes" in d:
            raise ConstraintError(name, "give nodes or len, not both")
        key = "nodes" if "nodes" in d else "len"
        _need(name, d, key, "each")
        shape = d.get("shape", "any")
        if shape not in SHAPES:
            raise ConstraintError(name, f"shape must be one of {', '.join(SHAPES)}")
        return ParamSpec(
            name,
            t,
            length=_bound(name, key, d[key], allow_single=True),
            each=_bound(name, "each", d["each"]),
            shape=shape,
            distinct=_flag(name, "distinct", d.get("distinct", False)) or shape == "bst",
        )

    assert t.kind == "array"
    if t.depth == 1:
        if t.base in ("int", "long", "double"):
            keys = {"len", "each", "distinct", "sorted", "perm"}
            _only(name, d, keys, t)
            _need(name, d, "len")
            perm = d.get("perm")
            if perm is not None:
                if not integral:
                    raise ConstraintError(name, "perm only applies to int/long arrays")
                if perm not in (0, 1) or isinstance(perm, bool):
                    raise ConstraintError(name, "perm must be 0 or 1 (the smallest value)")
                if "each" in d:
                    raise ConstraintError(name, "a permutation needs no 'each'")
            else:
                _need(name, d, "each")
            return ParamSpec(
                name,
                t,
                length=_bound(name, "len", d["len"], allow_single=True),
                each=_bound(name, "each", d["each"]) if "each" in d else None,
                distinct=_flag(name, "distinct", d.get("distinct", False)) or perm is not None,
                sorted=_sorted(name, d),
                perm=perm,
            )
        if t.base == "bool":
            _only(name, d, {"len"}, t)
            _need(name, d, "len")
            return ParamSpec(name, t, length=_bound(name, "len", d["len"], allow_single=True))
        if t.base == "char":
            _only(name, d, {"len", "charset", "each"}, t)
            _need(name, d, "len")
            cs = _charset_key(name, d)
            if cs is None:
                raise ConstraintError(name, "missing 'charset'")
            return ParamSpec(
                name, t, length=_bound(name, "len", d["len"], allow_single=True), charset=cs
            )
        _only(name, d, {"len", "each_len", "charset", "each"}, t)  # string[]
        _need(name, d, "len", "each_len")
        cs = _charset_key(name, d)
        if cs is None:
            raise ConstraintError(name, "missing 'charset'")
        return ParamSpec(
            name,
            t,
            length=_bound(name, "len", d["len"], allow_single=True),
            each_len=_bound(name, "each_len", d["each_len"], allow_single=True),
            charset=cs,
        )

    # depth 2
    if "graph" in d:
        if t.base not in ("int", "long"):
            raise ConstraintError(name, "graph needs an int[][] or long[][] parameter")
        _only(name, d, {"graph"}, t)
        return ParamSpec(name, t, graph=_graph(name, d["graph"]))
    if t.base in ("int", "long", "double"):
        _only(name, d, {"rows", "cols", "each", "ragged"}, t)
        _need(name, d, "rows", "cols", "each")
        each = _bound(name, "each", d["each"])
    elif t.base == "bool":
        _only(name, d, {"rows", "cols", "ragged"}, t)
        _need(name, d, "rows", "cols")
        each = None
    elif t.base == "char":
        _only(name, d, {"rows", "cols", "charset", "each", "ragged"}, t)
        _need(name, d, "rows", "cols")
        each = None
    else:
        raise ConstraintError(name, "string[][] is not supported")
    cs = _charset_key(name, d) if t.base == "char" else None
    if t.base == "char" and cs is None:
        raise ConstraintError(name, "missing 'charset' (or each: \"01\")")
    return ParamSpec(
        name,
        t,
        length=_bound(name, "rows", d["rows"], allow_single=True),
        cols=_bound(name, "cols", d["cols"], allow_single=True),
        each=each,
        charset=cs,
        ragged=_flag(name, "ragged", d.get("ragged", False)),
    )


def _sorted(param: str, d: dict[str, Any]) -> SortMode | None:
    s = d.get("sorted")
    if s is None or s is False:
        return None
    if s not in SORT_MODES:
        raise ConstraintError(param, f"sorted must be one of {', '.join(SORT_MODES)}")
    return s  # type: ignore[no-any-return]


def parse_constraints(part: Part) -> list[ParamSpec]:
    """Typed specs in signature order, validated against the part's parameters."""
    sig = part.sig
    names = [p.name for p in sig.params]
    for key in part.constraints:
        if key not in names:
            raise ConstraintError(key, f"not a parameter of {sig.method}({', '.join(names)})")
    specs: list[ParamSpec] = []
    for p in sig.params:
        raw = part.constraints.get(p.name)
        if raw is None:
            if p.tnode.kind == "scalar" and p.tnode.base == "bool":
                raw = {}
            else:
                raise ConstraintError(p.name, "no constraint given for this parameter")
        if not isinstance(raw, dict):
            raise ConstraintError(p.name, f"expected a mapping, got {raw!r}")
        spec = _parse_param(p.name, p.tnode, raw)
        earlier = {s.name for s in specs}
        for b in spec.all_bounds():
            for ref in b.names():
                if ref == p.name:
                    raise ConstraintError(p.name, "a bound cannot refer to the parameter itself")
                if ref not in earlier:
                    hint = (
                        f" ({ref!r} comes later in the signature)"
                        if ref in names
                        else f" (no parameter named {ref!r})"
                    )
                    raise ConstraintError(
                        p.name, f"bound {b} must refer to earlier parameters only{hint}"
                    )
        specs.append(spec)
    used: set[str] = set()
    for s in specs:
        for b in s.size_bounds():
            used |= b.names()
    return [
        ParamSpec(**{**s.__dict__, "is_size": s.name in used and s.range is not None})
        for s in specs
    ]
