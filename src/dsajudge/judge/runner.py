"""Batch execution of a built solution over test files (PLAN.md §5.2).

One process runs many tests. The driver streams one JSON result line per test over a
dedicated fd, so the runner always knows which test is running. A process that stops
answering is killed and the running test gets TLE; a process that dies is attributed to the
running test, re-running it alone first when earlier tests ran in the same process.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Literal

from dsajudge.judge.limits import MemoryBackend, RssWatchdog, kill_group, wrap_command
from dsajudge.lang.base import Built

Status = Literal["ok", "exception", "oom", "stackoverflow", "input", "tle", "mle", "crash"]
CAPTURE_LIMIT = 64 * 1024


@dataclass(frozen=True)
class TestCase:
    id: str
    path: Path


@dataclass
class RawResult:
    id: str
    status: Status
    time_ms: float = 0.0
    mem_kb: int = 0
    output: Any = None
    message: str = ""

    @property
    def completed(self) -> bool:
        return self.status == "ok"


@dataclass
class RunLimits:
    time_ms: float | None  # per-test limit; None = no TL (ref runs use wall_s only)
    memory_mb: int | None  # None = no memory enforcement
    wall_s: float | None = None  # per-test wall allowance override

    def per_test_wall(self) -> float:
        if self.wall_s is not None:
            return self.wall_s
        assert self.time_ms is not None
        return self.time_ms / 1000 * 1.5 + 0.5


@dataclass
class BatchOutcome:
    results: list[RawResult] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""


class _Capture(threading.Thread):
    """Drain a pipe, keeping the first CAPTURE_LIMIT bytes."""

    def __init__(self, stream: IO[bytes]) -> None:
        super().__init__(daemon=True)
        self.stream = stream
        self.buf = bytearray()
        self.truncated = False

    def run(self) -> None:
        while True:
            chunk = (
                self.stream.read1(65536)
                if hasattr(self.stream, "read1")
                else self.stream.read(65536)
            )
            if not chunk:
                break
            room = CAPTURE_LIMIT - len(self.buf)
            if room > 0:
                self.buf += chunk[:room]
            if len(chunk) > room:
                self.truncated = True

    def text(self) -> str:
        s = self.buf.decode("utf-8", errors="replace")
        return s + ("\n... (output truncated)" if self.truncated else "")


def _clean_stderr(text: str) -> str:
    return "\n".join(
        ln for ln in text.splitlines() if not ln.startswith("Picked up JAVA_TOOL_OPTIONS")
    )


def _tail(text: str, lines: int = 30) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


@dataclass
class _ProcessRun:
    results: list[RawResult]
    stdout: str
    stderr: str
    # Why the process ended before finishing every test, if it did.
    failure: Literal["timeout", "memory", "died"] | None = None
    failure_detail: str = ""
    peak_kb: int = 0


def _parse_line(line: str) -> RawResult:
    data = json.loads(line)
    status = data.get("status", "exception")
    if status not in ("ok", "exception", "oom", "stackoverflow", "input"):
        status = "exception"
    return RawResult(
        id=str(data.get("id", "")),
        status=status,
        time_ms=float(data.get("time_ns", 0)) / 1e6,
        mem_kb=int(data.get("mem_kb", 0)),
        output=data.get("output"),
        message=str(data.get("message", "")),
    )


def _signal_name(code: int) -> str:
    try:
        return signal.Signals(code).name
    except ValueError:
        return f"signal {code}"


def _run_process(
    built: Built, tests: list[TestCase], limits: RunLimits, backend: MemoryBackend
) -> _ProcessRun:
    r_fd, w_fd = os.pipe()
    limit_mb = limits.memory_mb
    mem_arg = limit_mb if limit_mb is not None else 4096
    argv = built.command(w_fd, mem_arg)
    hard_mb = None if limit_mb is None or built.debug else limit_mb + built.mem_overhead_mb
    argv = wrap_command(argv, backend, hard_mb)
    env = {**os.environ, **built.env}
    try:
        proc = subprocess.Popen(
            argv,
            cwd=built.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(w_fd,),
            start_new_session=True,
            env=env,
        )
    finally:
        os.close(w_fd)
    assert proc.stdin and proc.stdout and proc.stderr

    out_cap, err_cap = _Capture(proc.stdout), _Capture(proc.stderr)
    out_cap.start()
    err_cap.start()
    watchdog = RssWatchdog(proc.pid, hard_mb * 1024 if hard_mb is not None else None)
    watchdog.start()

    lines: queue.Queue[str | None] = queue.Queue()

    def reader() -> None:
        with os.fdopen(r_fd, "r", encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                lines.put(ln)
        lines.put(None)

    def writer() -> None:
        stdin = proc.stdin
        assert stdin is not None
        try:
            for t in tests:
                stdin.write(f"{t.id}\t{t.path}\n".encode())
            stdin.close()
        except (BrokenPipeError, OSError):
            pass

    threading.Thread(target=reader, daemon=True).start()
    threading.Thread(target=writer, daemon=True).start()

    results: list[RawResult] = []
    failure: Literal["timeout", "memory", "died"] | None = None
    start_allowance = built.startup_s
    last_event = time.monotonic()
    eof = False
    while len(results) < len(tests):
        allowance = limits.per_test_wall() + (start_allowance if not results else 0.0)
        now = time.monotonic()
        remaining = last_event + allowance - now
        if remaining <= 0:
            failure = "timeout"
            kill_group(proc.pid)
            break
        try:
            item = lines.get(timeout=min(remaining, 0.05))
        except queue.Empty:
            if watchdog.killed:
                failure = "memory"
                break
            continue
        if item is None:
            eof = True
            break
        try:
            results.append(_parse_line(item))
        except (ValueError, TypeError):
            continue
        last_event = time.monotonic()

    if eof and len(results) < len(tests):
        failure = "died"
    if failure is None and len(results) < len(tests):
        failure = "died"

    # Reap with rusage. Give a finished driver a moment to exit on its own.
    if failure is None:
        deadline = time.monotonic() + 5
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
    if proc.poll() is None:
        kill_group(proc.pid)
    try:
        _, status, rusage = os.wait4(proc.pid, 0)
        proc.returncode = os.waitstatus_to_exitcode(status)
        peak_kb = int(rusage.ru_maxrss)
    except ChildProcessError:
        proc.wait()
        peak_kb = 0
    watchdog.stop()
    out_cap.join(timeout=2)
    err_cap.join(timeout=2)
    stderr = _clean_stderr(err_cap.text())

    detail = ""
    if failure == "died":
        if watchdog.killed:
            failure = "memory"
        rc = proc.returncode
        if rc is not None and rc < 0:
            sig = -rc
            if sig == signal.SIGKILL and backend.kind == "systemd" and hard_mb is not None:
                failure = "memory"
            detail = f"killed by {_signal_name(sig)}"
        else:
            detail = f"exited with code {rc}"
    return _ProcessRun(
        results=results,
        stdout=out_cap.text(),
        stderr=stderr,
        failure=failure,
        failure_detail=detail,
        peak_kb=max(peak_kb, watchdog.peak_kb),
    )


def _apply_limits(r: RawResult, built: Built, limits: RunLimits) -> RawResult:
    """Post-hoc TLE/MLE on a completed test."""
    if r.status == "oom":
        r.status = "mle"
        return r
    if r.status not in ("ok",):
        return r
    if limits.time_ms is not None and r.time_ms > limits.time_ms:
        r.status = "tle"
        r.message = f"took {r.time_ms:.0f} ms, limit {limits.time_ms:.0f} ms"
    elif (
        limits.memory_mb is not None
        and not built.debug
        and r.mem_kb > (limits.memory_mb + built.mem_overhead_mb) * 1024
    ):
        r.status = "mle"
        r.message = f"peak memory {r.mem_kb / 1024:.0f} MB, limit {limits.memory_mb} MB"
    return r


def _culprit_result(run: _ProcessRun, test: TestCase, limits: RunLimits) -> RawResult:
    if run.failure == "timeout":
        tl = limits.time_ms
        msg = f"no result within {tl:.0f} ms" if tl else "no result within the time limit"
        return RawResult(test.id, "tle", time_ms=float(tl or 0), message=msg)
    if run.failure == "memory":
        lim = limits.memory_mb
        return RawResult(
            test.id, "mle", mem_kb=run.peak_kb, message=f"memory limit {lim} MB exceeded"
        )
    msg = run.failure_detail
    if run.stderr.strip():
        msg += "\n" + _tail(run.stderr)
    return RawResult(test.id, "crash", mem_kb=run.peak_kb, message=msg.strip())


def run_tests(
    built: Built,
    tests: list[TestCase],
    limits: RunLimits,
    backend: MemoryBackend,
    *,
    stop_on_failure: bool = True,
) -> BatchOutcome:
    """Run ``tests`` in batches; returns one RawResult per test that ran, in order."""
    outcome = BatchOutcome()
    pending = list(tests)
    while pending:
        run = _run_process(built, pending, limits, backend)
        outcome.stdout += run.stdout
        if run.stderr:
            outcome.stderr += run.stderr + "\n"
        for r in run.results:
            outcome.results.append(_apply_limits(r, built, limits))
        if run.failure is None:
            break
        idx = len(run.results)
        culprit = pending[idx]
        if run.failure == "died" and idx > 0:
            # State from earlier tests (C++ globals, a corrupted heap) may be to blame:
            # re-run the culprit alone for an authoritative verdict.
            solo = _run_process(built, [culprit], limits, backend)
            outcome.stdout += solo.stdout
            if solo.results:
                result = _apply_limits(solo.results[0], built, limits)
            else:
                result = _culprit_result(solo, culprit, limits)
        else:
            result = _culprit_result(run, culprit, limits)
        outcome.results.append(result)
        if stop_on_failure:
            break
        pending = pending[idx + 1 :]
    return outcome
