"""Output checkers (PLAN.md §4.2 `checker`)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from dsajudge.model.canon import canon_equal, dumps, float_equal

Checker = Callable[[Any, Any], bool]


class CheckerError(ValueError):
    pass


def _sort_key(v: Any) -> str:
    return dumps(v)


def check_exact(expected: Any, got: Any) -> bool:
    return canon_equal(expected, got)


def check_unordered(expected: Any, got: Any) -> bool:
    """Top-level list order doesn't matter."""
    if not (isinstance(expected, list) and isinstance(got, list)):
        return canon_equal(expected, got)
    if len(expected) != len(got):
        return False
    return canon_equal(sorted(expected, key=_sort_key), sorted(got, key=_sort_key))


def _deep_sort(v: Any) -> Any:
    if isinstance(v, list):
        return sorted((_deep_sort(x) for x in v), key=_sort_key)
    return v


def check_unordered_deep(expected: Any, got: Any) -> bool:
    """Order doesn't matter at any nesting level (e.g. groups of anagrams)."""
    return canon_equal(_deep_sort(expected), _deep_sort(got))


def get_checker(spec: str) -> Checker:
    if spec == "exact":
        return check_exact
    if spec == "unordered":
        return check_unordered
    if spec == "unordered_deep":
        return check_unordered_deep
    if spec.startswith("float:"):
        eps = float(spec.split(":", 1)[1])
        return lambda e, g: float_equal(e, g, eps)
    if spec == "custom":
        raise CheckerError("custom checkers (checker.py) arrive in Phase 2")
    raise CheckerError(f"unknown checker {spec!r}")
