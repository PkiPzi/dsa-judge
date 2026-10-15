"""Run stdio programs (PLAN.md §6.6): one process per test, stdin from a file, stdout compared.

Time is wall clock around the process minus the language's fixed start cost (JVM boot,
interpreter start), so a trivial program is never charged for launching.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from dsajudge.config import cache_dir
from dsajudge.judge.cache import digest
from dsajudge.judge.limits import MemoryBackend, RssWatchdog, kill_group, wrap_command
from dsajudge.judge.runner import (
    BatchOutcome,
    RawResult,
    RunLimits,
    TestCase,
    _Capture,
    _clean_stderr,
    _signal_name,
    _tail,
)
from dsajudge.lang.base import Program
from dsajudge.model.render import normalize_output

STDOUT_LIMIT = 32 * 1024 * 1024


def stdin_file(args_text: str, rendered: str) -> Path:
    """Write ``rendered`` stdin text to the cache (content-addressed) and return its path."""
    d = cache_dir() / "stdin"
    path = d / (digest(args_text, rendered)[:40] + ".txt")
    if not path.exists():
        d.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(rendered, encoding="utf-8")
        tmp.replace(path)
    return path


def run_program(
    program: Program, stdin_path: Path, test_id: str, limits: RunLimits, backend: MemoryBackend
) -> tuple[RawResult, str]:
    """Run one test. Returns the result and the program's stderr."""
    mem_arg = limits.memory_mb if limits.memory_mb is not None else 4096
    hard_mb = (
        None
        if limits.memory_mb is None or program.debug
        else limits.memory_mb + program.mem_overhead_mb
    )
    argv = wrap_command(program.command(mem_arg), backend, hard_mb)
    wall = limits.per_test_wall() + program.startup_ms / 1000 + 1.0
    with stdin_path.open("rb") as fin:
        t0 = time.monotonic()
        proc = subprocess.Popen(
            argv,
            cwd=program.cwd,
            stdin=fin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env={**os.environ, **program.env},
        )
        assert proc.stdout and proc.stderr
        out_cap = _Capture(proc.stdout, STDOUT_LIMIT)
        err_cap = _Capture(proc.stderr)
        out_cap.start()
        err_cap.start()
        watchdog = RssWatchdog(proc.pid, hard_mb * 1024 if hard_mb is not None else None)
        watchdog.start()
        timed_out = False
        status = 0
        while True:
            pid, status, _ = os.wait4(proc.pid, os.WNOHANG)
            if pid:
                break
            if time.monotonic() - t0 > wall:
                timed_out = True
                kill_group(proc.pid)
                _, status, _ = os.wait4(proc.pid, 0)
                break
            time.sleep(0.002)
        elapsed_ms = (time.monotonic() - t0) * 1000
    watchdog.stop()
    out_cap.join(timeout=2)
    err_cap.join(timeout=2)
    kill_group(proc.pid)  # stragglers holding the pipes
    proc.returncode = os.waitstatus_to_exitcode(status)
    stderr = _clean_stderr(err_cap.text())
    peak_kb = watchdog.peak_kb  # not ru_maxrss: see limits.read_mem_kb
    time_ms = max(elapsed_ms - program.startup_ms, 0.0)

    if timed_out:
        tl = limits.time_ms
        msg = f"no result within {tl:.0f} ms" if tl else "no result within the time limit"
        return RawResult(test_id, "tle", time_ms=float(tl or time_ms), message=msg), stderr
    rc = proc.returncode
    if watchdog.killed or (
        rc is not None
        and rc == -signal.SIGKILL
        and backend.kind == "systemd"
        and hard_mb is not None
    ):
        lim = limits.memory_mb
        return (
            RawResult(
                test_id,
                "mle",
                time_ms=time_ms,
                mem_kb=peak_kb,
                message=f"memory limit {lim} MB exceeded",
            ),
            stderr,
        )
    if rc != 0:
        detail = f"killed by {_signal_name(-rc)}" if rc < 0 else f"exited with code {rc}"
        if "OutOfMemoryError" in stderr:
            return (
                RawResult(test_id, "mle", time_ms=time_ms, mem_kb=peak_kb, message=_tail(stderr)),
                stderr,
            )
        msg = detail + ("\n" + _tail(stderr) if stderr.strip() else "")
        return RawResult(test_id, "crash", time_ms=time_ms, mem_kb=peak_kb, message=msg), stderr
    if out_cap.truncated:
        return (
            RawResult(
                test_id,
                "crash",
                time_ms=time_ms,
                mem_kb=peak_kb,
                message=f"output is larger than {STDOUT_LIMIT // (1024 * 1024)} MB",
            ),
            stderr,
        )
    text = normalize_output(out_cap.buf.decode("utf-8", errors="replace"))
    return RawResult(test_id, "ok", time_ms=time_ms, mem_kb=peak_kb, output=text), stderr


def apply_limits(r: RawResult, program: Program, limits: RunLimits) -> RawResult:
    if r.status != "ok":
        return r
    if limits.time_ms is not None and r.time_ms > limits.time_ms:
        r.status = "tle"
        r.message = f"took {r.time_ms:.0f} ms, limit {limits.time_ms:.0f} ms"
    elif (
        limits.memory_mb is not None
        and not program.debug
        and r.mem_kb > (limits.memory_mb + program.mem_overhead_mb) * 1024
    ):
        r.status = "mle"
        r.message = f"peak memory {r.mem_kb / 1024:.0f} MB, limit {limits.memory_mb} MB"
    return r


def run_program_tests(
    program: Program,
    tests: list[TestCase],
    stdin_paths: dict[str, Path],
    limits: RunLimits,
    backend: MemoryBackend,
    *,
    stop_on_failure: bool = True,
) -> BatchOutcome:
    """Run every test in its own process. ``stdin_paths`` maps test id to its stdin file."""
    outcome = BatchOutcome()
    for t in tests:
        result, stderr = run_program(program, stdin_paths[t.id], t.id, limits, backend)
        result = apply_limits(result, program, limits)
        outcome.results.append(result)
        if stderr:
            outcome.stderr = stderr
        if result.status != "ok" and stop_on_failure:
            break
    return outcome
