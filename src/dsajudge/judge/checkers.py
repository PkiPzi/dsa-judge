"""Output checkers (PLAN.md §4.2 `checker`)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from dsajudge.config import JudgeConfig
from dsajudge.judge.custom_checker import CheckerFailure, CustomChecker
from dsajudge.model.canon import canon_equal, dumps, float_equal
from dsajudge.model.render import get_text_checker
from dsajudge.model.schema import Part

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


class PartChecker:
    """Decides whether an answer is right: canonical-value comparison, token comparison in
    stdio mode, or a custom ``checker.py`` that also needs the test's input."""

    def __init__(self, fn: Checker | None = None, custom: CustomChecker | None = None) -> None:
        self.fn = fn
        self.custom = custom

    @property
    def needs_args(self) -> bool:
        return self.custom is not None

    def verdict(self, expected: Any, got: Any, args: list[Any] | None = None) -> tuple[bool, str]:
        if self.custom is not None:
            if args is None:
                raise CheckerError("a custom checker needs the test's input")
            try:
                return self.custom.check(args, expected, got)
            except CheckerFailure as e:
                raise CheckerError(str(e)) from e
        assert self.fn is not None
        return self.fn(expected, got), ""

    def __call__(self, expected: Any, got: Any, args: list[Any] | None = None) -> bool:
        return self.verdict(expected, got, args)[0]

    def close(self) -> None:
        if self.custom is not None:
            self.custom.close()


def part_checker(
    part: Part, problem_dir: Path | None = None, jcfg: JudgeConfig | None = None
) -> PartChecker:
    """The comparison for a part; ``problem_dir`` and ``jcfg`` are needed for ``custom``."""
    if part.checker == "custom":
        if problem_dir is None or jcfg is None:
            raise CheckerError("a custom checker needs the problem directory")
        script = problem_dir / f"part{part.id}" / "checker.py"
        if not script.is_file():
            raise CheckerError(
                f"part {part.id} uses checker: custom but part{part.id}/checker.py is missing"
            )
        return PartChecker(custom=CustomChecker(script, jcfg.python_exe()))
    if part.mode == "stdio":
        return PartChecker(get_text_checker(part.checker))
    return PartChecker(get_checker(part.checker))


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
        raise CheckerError("custom checkers need part_checker() with the problem directory")
    raise CheckerError(f"unknown checker {spec!r}")
