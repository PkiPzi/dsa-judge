"""stdio mode: render generated params to program input, expected results to program output.

``input_format`` is a template whose ``{name}`` placeholders are replaced by the parameter's
text. ``{expr}`` also works (``{len(nums)}``, ``{n - 1}``), and ``{name:lines}`` /
``{name:concat}`` pick the layout of arrays:

* scalar: decimal; bool is ``1``/``0``; strings and chars are raw
* ``T[]``: space separated (``:lines`` one per line, ``:concat`` no separator)
* ``T[][]``: one row per line, each row space separated (``:concat`` joins a row without spaces,
  for character grids)
* ``ListNode``: like ``int[]``; ``TreeNode``: level order with ``null`` for missing children

Write ``{{`` and ``}}`` for literal braces.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from dsajudge.model.expr import ExprError, parse_expr
from dsajudge.model.schema import Part
from dsajudge.model.types import TypeNode

_PLACEHOLDER = re.compile(r"\{\{|\}\}|\{([^{}]*)\}")
SPECS = ("", "lines", "concat")


class FormatError(ValueError):
    pass


def _scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        text = repr(v)
        if "e" in text or "E" in text:
            text = f"{v:.12f}".rstrip("0").rstrip(".")
        return text
    if v is None:
        return "null"
    return str(v)


def render_value(t: TypeNode, v: Any, spec: str = "") -> str:
    if t.kind == "array" and t.depth == 2:
        sep = "" if spec == "concat" else " "
        return "\n".join(sep.join(_scalar(x) for x in row) for row in v)
    if t.kind in ("array", "listnode", "treenode"):
        if spec == "lines":
            return "\n".join(_scalar(x) for x in v)
        sep = "" if spec == "concat" else " "
        return sep.join(_scalar(x) for x in v)
    return _scalar(v)


def _expand(template: str, values: dict[str, tuple[TypeNode, Any]], where: str) -> str:
    env = {k: v for k, (_, v) in values.items()}

    def repl(m: re.Match[str]) -> str:
        if m.group(0) == "{{":
            return "{"
        if m.group(0) == "}}":
            return "}"
        body = m.group(1).strip()
        expr, _, spec = body.partition(":")
        expr, spec = expr.strip(), spec.strip()
        if spec not in SPECS:
            raise FormatError(
                f"{where}: unknown layout {spec!r} in {{{body}}} (use lines or concat)"
            )
        if expr in values:
            t, v = values[expr]
            return render_value(t, v, spec)
        try:
            return _scalar(parse_expr(expr).eval(env))
        except ExprError as e:
            raise FormatError(f"{where}: cannot render {{{body}}}: {e}") from e

    return _PLACEHOLDER.sub(repl, template)


def check_format(template: str, names: list[str], where: str) -> None:
    """Raise FormatError if a placeholder refers to an unknown name or layout."""
    dummy = {n: (TypeNode("scalar", "int"), 1) for n in names}
    for m in _PLACEHOLDER.finditer(template):
        if m.group(1) is None:
            continue
        expr, _, spec = m.group(1).strip().partition(":")
        if spec.strip() not in SPECS:
            raise FormatError(f"{where}: unknown layout {spec.strip()!r} (use lines or concat)")
        expr = expr.strip()
        if expr in dummy:
            continue
        try:
            e = parse_expr(expr)
        except ExprError as err:
            raise FormatError(f"{where}: {{{m.group(1)}}}: {err}") from err
        unknown = e.names() - set(names)
        if unknown:
            raise FormatError(f"{where}: {{{m.group(1)}}} uses unknown name {sorted(unknown)[0]!r}")


def render_input(part: Part, args: list[Any]) -> str:
    """The text a stdio program reads on stdin for these generated args."""
    assert part.input_format is not None
    values = {p.name: (p.tnode, a) for p, a in zip(part.sig.params, args, strict=True)}
    text = _expand(part.input_format, values, "input_format")
    return text if text.endswith("\n") else text + "\n"


def render_output(part: Part, result: Any) -> str:
    template = part.output_format or "{result}"
    values = {"result": (part.sig.output_type, result)}
    return normalize_output(_expand(template, values, "output_format"))


def normalize_output(text: str) -> str:
    """Strip trailing whitespace on each line and trailing blank lines."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


# ---------------------------------------------------------------- token checkers

TextChecker = Callable[[str, str], bool]


def _tokens(text: str) -> list[str]:
    return text.split()


def _float_tokens_equal(a: str, b: str, eps: float) -> bool:
    if a == b:
        return True
    try:
        x, y = float(a), float(b)
    except ValueError:
        return False
    if x != x or y != y:
        return False
    return abs(x - y) <= eps * max(1.0, abs(x), abs(y))


def get_text_checker(spec: str) -> TextChecker:
    if spec == "exact":
        return lambda e, g: _tokens(e) == _tokens(g)
    if spec in ("unordered", "unordered_deep"):
        return lambda e, g: sorted(_tokens(e)) == sorted(_tokens(g))
    if spec.startswith("float:"):
        eps = float(spec.split(":", 1)[1])

        def check(e: str, g: str) -> bool:
            te, tg = _tokens(e), _tokens(g)
            return len(te) == len(tg) and all(
                _float_tokens_equal(x, y, eps) for x, y in zip(te, tg, strict=True)
            )

        return check
    raise FormatError(f"checker {spec!r} is not supported in stdio mode")


def first_token_difference(expected: str, got: str) -> str | None:
    te, tg = _tokens(expected), _tokens(got)
    for i, (x, y) in enumerate(zip(te, tg, strict=False)):
        if x != y:
            return f"token {i + 1}: got {y!r}, expected {x!r}"
    if len(te) != len(tg):
        return f"got {len(tg)} token(s), expected {len(te)}"
    return None
