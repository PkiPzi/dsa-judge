"""Verdicts and judge reports (PLAN.md §5.3)."""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from rich.console import Console
from rich.markup import escape

from dsajudge.model.canon import dumps, first_difference, shape

SMALL_IO_BYTES = 2048


class Verdict(StrEnum):
    AC = "AC"
    WA = "WA"
    WA_UNVERIFIED = "WA?"
    TLE = "TLE"
    MLE = "MLE"
    RE = "RE"
    CE = "CE"
    JE = "JE"  # judge error: bad test input, broken oracle


LABELS = {
    Verdict.AC: "Accepted",
    Verdict.WA: "Wrong Answer",
    Verdict.WA_UNVERIFIED: "WA? (unverified oracle)",
    Verdict.TLE: "Time Limit Exceeded",
    Verdict.MLE: "Memory Limit Exceeded",
    Verdict.RE: "Runtime Error",
    Verdict.CE: "Compile Error",
    Verdict.JE: "Judge Error",
}

COLORS = {
    Verdict.AC: "green",
    Verdict.WA: "red",
    Verdict.WA_UNVERIFIED: "yellow",
    Verdict.TLE: "magenta",
    Verdict.MLE: "magenta",
    Verdict.RE: "red",
    Verdict.CE: "yellow",
    Verdict.JE: "yellow",
}


@dataclass
class TestVerdict:
    id: str
    verdict: Verdict
    time_ms: float = 0.0
    mem_kb: int = 0
    args: list[Any] | None = None
    param_names: list[str] = field(default_factory=list)
    expected: Any = None
    has_expected: bool = False
    got: Any = None
    has_got: bool = False
    message: str = ""
    run_only: bool = False

    @property
    def passed(self) -> bool:
        return self.verdict == Verdict.AC


@dataclass
class Report:
    verdict: Verdict
    total: int
    results: list[TestVerdict] = field(default_factory=list)
    compile_output: str = ""
    stdout: str = ""
    stderr: str = ""
    tl_ms: float = 0.0
    memory_mb: int = 0
    all_tests: bool = False
    elapsed_min: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def first_failure(self) -> TestVerdict | None:
        return next((r for r in self.results if not r.passed), None)

    @property
    def passed(self) -> int:
        if self.all_tests:
            return sum(r.passed for r in self.results)
        n = 0
        for r in self.results:
            if not r.passed:
                break
            n += 1
        return n

    @property
    def max_time_ms(self) -> float:
        return max((r.time_ms for r in self.results), default=0.0)

    @property
    def max_mem_kb(self) -> int:
        return max((r.mem_kb for r in self.results), default=0)


def overall(results: list[TestVerdict], total: int) -> Verdict:
    for r in results:
        if not r.passed:
            return r.verdict
    return Verdict.AC if len(results) == total else Verdict.JE


# ---------------------------------------------------------------- rendering


def _fmt_args(r: TestVerdict) -> str:
    if r.args is None:
        return ""
    if r.param_names and len(r.param_names) == len(r.args):
        return ", ".join(f"{n} = {dumps(a)}" for n, a in zip(r.param_names, r.args, strict=True))
    return "\n".join(dumps(a) for a in r.args)


def _io_size(r: TestVerdict) -> int:
    size = len(_fmt_args(r))
    if r.has_expected:
        size += len(dumps(r.expected))
    if r.has_got:
        size += len(dumps(r.got))
    return size


def _diff(expected: Any, got: Any) -> list[str]:
    a = json.dumps(expected, indent=1, ensure_ascii=False).splitlines()
    b = json.dumps(got, indent=1, ensure_ascii=False).splitlines()
    if len(a) <= 1 and len(b) <= 1:
        return []
    return list(difflib.unified_diff(a, b, "expected", "got", lineterm="", n=1))[:60]


def render_failure(console: Console, r: TestVerdict) -> None:
    console.print(f"  test: [bold]{escape(r.id)}[/]")
    if r.message:
        for line in r.message.splitlines()[:40]:
            console.print(f"  [dim]{escape(line)}[/]")
    if r.args is None:
        return
    if _io_size(r) <= SMALL_IO_BYTES:
        console.print(f"  input:    {escape(_fmt_args(r))}")
        if r.has_expected:
            console.print(f"  expected: {escape(dumps(r.expected))}")
        if r.has_got:
            console.print(f"  got:      {escape(dumps(r.got))}")
        if r.has_expected and r.has_got:
            for line in _diff(r.expected, r.got):
                style = (
                    "red" if line.startswith("-") else "green" if line.startswith("+") else "dim"
                )
                console.print(f"    [{style}]{escape(line)}[/]")
    else:
        sizes = ", ".join(
            f"{n}: {shape(a)}" for n, a in zip(r.param_names or [], r.args, strict=False)
        )
        console.print(f"  input sizes: {escape(sizes)}")
        if r.has_expected and r.has_got:
            d = first_difference(r.expected, r.got)
            if d:
                console.print(f"  first difference: {escape(d)}")


def render_report(
    console: Console, report: Report, *, strict: bool = False, label: str = ""
) -> None:
    v = report.verdict
    color = COLORS[v]
    head = f"[bold {color}]{LABELS[v]}[/]"
    if label:
        head += f"  [dim]{escape(label)}[/]"
    console.print(head)
    if v == Verdict.CE:
        if not strict:
            console.print(escape(report.compile_output.strip()))
        return
    for note in report.notes:
        console.print(f"[yellow]note:[/] {escape(note)}")
    if strict:
        console.print(f"passed {report.passed}/{report.total}")
        return
    failures = [r for r in report.results if not r.passed]
    if not report.all_tests:
        failures = failures[:1]
    for r in failures:
        console.print(f"[{COLORS[r.verdict]}]{LABELS[r.verdict]}[/]")
        render_failure(console, r)
    if report.stdout.strip():
        console.print("[dim]stdout:[/]")
        console.print(escape(report.stdout.rstrip()[:4000]))
    parts = [
        f"passed {report.passed}/{report.total}",
        f"max {report.max_time_ms:.0f} ms (limit {report.tl_ms:.0f})",
        f"{report.max_mem_kb / 1024:.1f} MB",
    ]
    if report.elapsed_min is not None:
        parts.append(f"{report.elapsed_min:.0f} min since dsa open")
    console.print("[dim]" + " · ".join(parts) + "[/]")
