"""Language interface shared by the C++, Java and Python backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from dsajudge.config import JudgeConfig
from dsajudge.errors import BUILD_PLACEHOLDER, CompileError
from dsajudge.model.schema import Lang, Part

__all__ = ["BUILD_PLACEHOLDER", "Built", "CompileError", "Language", "Program"]


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


@dataclass
class Program:
    """A compiled stdio program: reads stdin, writes stdout. ``argv`` may contain {mem}."""

    lang: Lang
    argv: list[str]
    cwd: Path
    mem_overhead_mb: int
    # Fixed process start cost (JVM boot, interpreter start) that is not charged to the solution.
    startup_ms: float
    debug: bool = False
    env: dict[str, str] = field(default_factory=dict)

    def command(self, memory_mb: int) -> list[str]:
        return [a.replace("{mem}", str(memory_mb)) for a in self.argv]


class Language(Protocol):
    name: Lang
    ext: str
    sol_filename: str

    def stub(self, slug: str, part: Part) -> str: ...

    def driver_files(self, slug: str, part: Part) -> dict[str, str]: ...

    def build(
        self, src: Path, slug: str, part: Part, cfg: JudgeConfig, *, debug: bool = False
    ) -> Built: ...

    def stdio_stub(self, slug: str, part: Part) -> str: ...

    def build_program(self, src: Path, cfg: JudgeConfig, *, debug: bool = False) -> Program: ...
