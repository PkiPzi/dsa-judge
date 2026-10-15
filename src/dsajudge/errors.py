"""Errors shared by the build cache and the language backends."""

from __future__ import annotations

from pathlib import Path

BUILD_PLACEHOLDER = "<build>"


class CompileError(Exception):
    """Compilation (or syntax check) failed. ``output`` uses <build>/ for the build dir."""

    def __init__(self, output: str) -> None:
        super().__init__(output)
        self.output = output

    def render(self, src: Path | None = None) -> str:
        text = self.output
        if src is not None:
            for name in ("sol.cpp", "Solution.java", "sol.py"):
                text = text.replace(f"{BUILD_PLACEHOLDER}/{name}", str(src))
        return text.replace(BUILD_PLACEHOLDER + "/", "")
