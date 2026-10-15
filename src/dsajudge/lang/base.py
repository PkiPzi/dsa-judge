"""Language interface shared by the C++, Java and Python backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from dsajudge.config import JudgeConfig
from dsajudge.model.schema import Lang, Part

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


@dataclass
class Built:
    """A runnable solution+driver. ``argv`` may contain {fd} and {mem} placeholders."""

    lang: Lang
    argv: list[str]
    cwd: Path
    # Extra resident memory allowed over memory_mb before the RSS watchdog kills the process.
    mem_overhead_mb: int
    # Extra time allowed for the first test (process start, JVM boot, imports).
    startup_s: float
    debug: bool = False
    env: dict[str, str] = field(default_factory=dict)

    def command(self, result_fd: int, memory_mb: int) -> list[str]:
        return [
            a.replace("{fd}", str(result_fd)).replace("{mem}", str(memory_mb)) for a in self.argv
        ]


class Language(Protocol):
    name: Lang
    ext: str
    sol_filename: str

    def stub(self, slug: str, part: Part) -> str: ...

    def driver_files(self, slug: str, part: Part) -> dict[str, str]: ...

    def build(
        self, src: Path, slug: str, part: Part, cfg: JudgeConfig, *, debug: bool = False
    ) -> Built: ...
