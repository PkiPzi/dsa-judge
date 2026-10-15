"""Canonical JSON I/O (PLAN.md §4.4).

Input files hold one JSON value per line, one line per parameter, in signature order.
Outputs are a single JSON value. Python's json module keeps integers exact.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def dumps(v: Any) -> str:
    """Compact canonical JSON for one value."""
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def args_to_text(args: list[Any]) -> str:
    return "".join(dumps(a) + "\n" for a in args)


def text_to_args(text: str) -> list[Any]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    try:
        return [json.loads(ln) for ln in lines]
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON in test input: {e}") from e


def read_args(path: Path) -> list[Any]:
    return text_to_args(path.read_text(encoding="utf-8"))


def write_args(path: Path, args: list[Any]) -> None:
    path.write_text(args_to_text(args), encoding="utf-8")


def canon_equal(a: Any, b: Any) -> bool:
    """Deep equality of parsed JSON values; bools never equal numbers."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(canon_equal(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(canon_equal(a[k], b[k]) for k in a)
    return type(a) is type(b) and bool(a == b)


def float_equal(a: Any, b: Any, eps: float) -> bool:
    """Like canon_equal, but numbers match within absolute or relative ``eps``."""
    if isinstance(a, bool) or isinstance(b, bool):
        return canon_equal(a, b)
    if isinstance(a, int | float) and isinstance(b, int | float):
        if math.isnan(a) or math.isnan(b):
            return False
        return abs(a - b) <= eps * max(1.0, abs(a), abs(b))
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(float_equal(x, y, eps) for x, y in zip(a, b, strict=True))
    return canon_equal(a, b)


def first_difference(expected: Any, got: Any, path: str = "") -> str | None:
    """Human-readable location of the first difference, or None if equal."""
    if isinstance(expected, list) and isinstance(got, list):
        for i, (x, y) in enumerate(zip(expected, got, strict=False)):
            d = first_difference(x, y, f"{path}[{i}]")
            if d:
                return d
        if len(expected) != len(got):
            return f"{path or 'output'}: length {len(got)}, expected {len(expected)}"
        return None
    if canon_equal(expected, got):
        return None
    return f"{path or 'output'}: got {short(got)}, expected {short(expected)}"


def short(v: Any, limit: int = 80) -> str:
    s = dumps(v)
    return s if len(s) <= limit else s[: limit - 3] + "..."


def shape(v: Any) -> str:
    """Compact size description: 'len 100000' or '300x300' or a short scalar."""
    if isinstance(v, list):
        if v and all(isinstance(r, list) for r in v):
            return f"{len(v)}x{max(len(r) for r in v)}"
        return f"len {len(v)}"
    if isinstance(v, str):
        return f"str len {len(v)}"
    return short(v, 40)
