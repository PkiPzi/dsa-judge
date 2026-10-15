"""Bound expressions for the constraint DSL (PLAN.md §6.1).

YAML loaders read ``1e5`` as a string, so every bound goes through this parser: it accepts
ints, floats and strings like ``1e5``, ``10^5``, ``2*10^5``, ``10^9 + 7`` and
``len(nums) - 1``. Integer arithmetic stays exact (``10^18`` is an int, never a float).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

Number = int | float

FUNCTIONS = ("len", "min", "max", "sum", "abs")
MAX_EXPONENT = 400

_TOKEN = re.compile(
    r"""\s*(?:
      (?P<num>(?:\d[\d_]*(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)
     |(?P<name>[A-Za-z_][A-Za-z0-9_]*)
     |(?P<op>//|\*\*|[-+*/^(),])
    )""",
    re.VERBOSE,
)

# OCR and copy-paste produce these look-alikes.
_NORMALIZE = str.maketrans({"−": "-", "–": "-", "×": "*", "·": "*", "÷": "/"})


class ExprError(ValueError):
    """A bound expression is malformed or cannot be evaluated."""


@dataclass(frozen=True)
class _Tok:
    kind: str  # num | name | op | end
    text: str


def _tokenize(src: str) -> list[_Tok]:
    toks: list[_Tok] = []
    pos = 0
    src = src.translate(_NORMALIZE)
    while pos < len(src):
        if src[pos:].strip() == "":
            break
        m = _TOKEN.match(src, pos)
        if not m or m.end() == pos:
            raise ExprError(f"unexpected character {src[pos:].lstrip()[0]!r} in {src!r}")
        pos = m.end()
        kind = m.lastgroup or ""
        toks.append(_Tok(kind, m.group(kind)))
    toks.append(_Tok("end", ""))
    return toks


def _literal(text: str) -> Number:
    t = text.replace("_", "")
    try:
        d = Decimal(t)
    except InvalidOperation as e:
        raise ExprError(f"bad number {text!r}") from e
    if d == d.to_integral_value():
        return int(d)
    return float(d)


# AST: ("num", v) | ("name", n) | ("neg", a) | ("bin", op, a, b) | ("call", f, [args])
Node = tuple[Any, ...]


class _Parser:
    def __init__(self, src: str) -> None:
        self.src = src
        self.toks = _tokenize(src)
        self.i = 0

    def peek(self) -> _Tok:
        return self.toks[self.i]

    def take(self) -> _Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def accept(self, op: str) -> bool:
        t = self.peek()
        if t.kind == "op" and t.text == op:
            self.i += 1
            return True
        return False

    def parse(self) -> Node:
        node = self.expr()
        if self.peek().kind != "end":
            raise ExprError(f"unexpected {self.peek().text!r} in {self.src!r}")
        return node

    def expr(self) -> Node:
        node = self.term()
        while True:
            if self.accept("+"):
                node = ("bin", "+", node, self.term())
            elif self.accept("-"):
                node = ("bin", "-", node, self.term())
            else:
                return node

    def term(self) -> Node:
        node = self.unary()
        while True:
            if self.accept("*"):
                node = ("bin", "*", node, self.unary())
            elif self.accept("//"):
                node = ("bin", "//", node, self.unary())
            elif self.accept("/"):
                node = ("bin", "/", node, self.unary())
            else:
                return node

    def unary(self) -> Node:
        if self.accept("-"):
            return ("neg", self.unary())
        if self.accept("+"):
            return self.unary()
        return self.power()

    def power(self) -> Node:
        base = self.atom()
        if self.accept("^") or self.accept("**"):
            return ("bin", "^", base, self.unary())
        return base

    def atom(self) -> Node:
        t = self.take()
        if t.kind == "num":
            return ("num", _literal(t.text))
        if t.kind == "name":
            if self.accept("("):
                if t.text not in FUNCTIONS:
                    raise ExprError(
                        f"unknown function {t.text!r} in {self.src!r} (use {', '.join(FUNCTIONS)})"
                    )
                args: list[Node] = []
                if not self.accept(")"):
                    args.append(self.expr())
                    while self.accept(","):
                        args.append(self.expr())
                    if not self.accept(")"):
                        raise ExprError(f"missing ')' in {self.src!r}")
                return ("call", t.text, args)
            if t.text in FUNCTIONS:
                raise ExprError(f"{t.text!r} is a function; write {t.text}(...)")
            return ("name", t.text)
        if t.kind == "op" and t.text == "(":
            node = self.expr()
            if not self.accept(")"):
                raise ExprError(f"missing ')' in {self.src!r}")
            return node
        raise ExprError(f"unexpected {t.text or 'end of expression'!r} in {self.src!r}")


def _names(node: Node, out: set[str]) -> None:
    kind = node[0]
    if kind == "name":
        out.add(node[1])
    elif kind == "neg":
        _names(node[1], out)
    elif kind == "bin":
        _names(node[2], out)
        _names(node[3], out)
    elif kind == "call":
        for a in node[2]:
            _names(a, out)


def _num(v: Any, what: str) -> Number:
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise ExprError(f"{what}: expected a number, got {type(v).__name__}")
    return v


def _eval(node: Node, env: dict[str, Any]) -> Any:
    kind = node[0]
    if kind == "num":
        return node[1]
    if kind == "name":
        if node[1] not in env:
            raise ExprError(f"unknown name {node[1]!r}")
        return env[node[1]]
    if kind == "neg":
        return -_num(_eval(node[1], env), "unary minus")
    if kind == "bin":
        op = node[1]
        a = _num(_eval(node[2], env), f"left of {op!r}")
        b = _num(_eval(node[3], env), f"right of {op!r}")
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "^":
            if abs(b) > MAX_EXPONENT:
                raise ExprError(f"exponent {b} is too large")
            return a**b
        if b == 0:
            raise ExprError("division by zero")
        if op == "//":
            return a // b
        # "/" keeps ints exact when it divides evenly.
        if isinstance(a, int) and isinstance(b, int) and a % b == 0:
            return a // b
        return a / b
    # call
    fn, args = node[1], [_eval(a, env) for a in node[2]]
    if fn == "len":
        if len(args) != 1 or not isinstance(args[0], list | str):
            raise ExprError("len() takes one array or string")
        return len(args[0])
    if fn == "abs":
        if len(args) != 1:
            raise ExprError("abs() takes one number")
        return abs(_num(args[0], "abs"))
    if fn == "sum":
        if len(args) != 1 or not isinstance(args[0], list):
            raise ExprError("sum() takes one array")
        return sum(_num(x, "sum") for x in args[0])
    # min / max: one array, or several numbers
    vals = args[0] if len(args) == 1 and isinstance(args[0], list) else args
    if not vals:
        raise ExprError(f"{fn}() of nothing")
    nums = [_num(x, fn) for x in vals]
    return min(nums) if fn == "min" else max(nums)


@dataclass(frozen=True)
class Expr:
    text: str
    node: Node

    def names(self) -> set[str]:
        out: set[str] = set()
        _names(self.node, out)
        return out

    def eval(self, env: dict[str, Any] | None = None) -> Number:
        value = _eval(self.node, env or {})
        return _num(value, self.text)

    def eval_int(self, env: dict[str, Any] | None = None, *, round_up: bool = False) -> int:
        """Integer value: floors by default, ceils with ``round_up`` (for lower bounds)."""
        v = self.eval(env)
        if isinstance(v, int):
            return v
        if v != v or v in (float("inf"), float("-inf")):
            raise ExprError(f"{self.text!r} is not finite")
        i = int(v)
        if i != v and round_up and v > 0:
            return i + 1
        if i != v and not round_up and v < 0:
            return i - 1
        return i

    def __str__(self) -> str:
        return self.text


def parse_expr(src: object) -> Expr:
    """Parse an int/float/str bound. Floats like 1e5 become exact ints when integral."""
    if isinstance(src, bool) or src is None:
        raise ExprError(f"expected a number or expression, got {src!r}")
    if isinstance(src, int):
        return Expr(str(src), ("num", src))
    if isinstance(src, float):
        if src != src or src in (float("inf"), float("-inf")):
            raise ExprError(f"{src!r} is not finite")
        v: Number = int(src) if src == int(src) else src
        return Expr(repr(src) if v is src else str(v), ("num", v))
    if isinstance(src, str):
        if not src.strip():
            raise ExprError("empty expression")
        return Expr(src.strip(), _Parser(src).parse())
    raise ExprError(f"expected a number or expression, got {src!r}")
