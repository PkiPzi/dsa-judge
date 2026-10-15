"""Run a part's ``checker.py`` (PLAN.md §4.1, §7.2) in an isolated worker process.

``def check(args, expected, got) -> tuple[bool, str]`` decides whether ``got`` is a valid
answer. The worker is one long-lived ``python -I`` process per checker, so thousands of
comparisons (a build) cost no process starts. A checker that hangs or crashes is a
CheckerFailure, never a silent verdict.
"""

from __future__ import annotations

import json
import select
import subprocess
import weakref
from pathlib import Path
from typing import Any

from dsajudge.gen.custom import HARNESS

REPLY_TIMEOUT_S = 10.0


class CheckerFailure(Exception):
    """checker.py raised, hung, or is malformed."""


def _stop(proc: subprocess.Popen[str]) -> None:
    try:
        proc.kill()
        proc.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        pass


class CustomChecker:
    def __init__(self, script: Path, python: str, timeout: float = REPLY_TIMEOUT_S) -> None:
        self.script = script
        self.python = python
        self.timeout = timeout
        self._proc: subprocess.Popen[str] | None = None
        self._finalizer: weakref.finalize[..., Any] | None = None

    def _start(self) -> subprocess.Popen[str]:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        self._proc = subprocess.Popen(
            [self.python, "-I", str(HARNESS), "check", str(self.script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=self.script.parent,
        )
        self._finalizer = weakref.finalize(self, _stop, self._proc)
        return self._proc

    def check(self, args: list[Any], expected: Any, got: Any) -> tuple[bool, str]:
        proc = self._start()
        assert proc.stdin and proc.stdout
        try:
            line = json.dumps({"args": args, "expected": expected, "got": got}, allow_nan=True)
            proc.stdin.write(line + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise CheckerFailure(f"{self.script.name} exited unexpectedly") from e
        ready, _, _ = select.select([proc.stdout], [], [], self.timeout)
        if not ready:
            _stop(proc)
            raise CheckerFailure(f"{self.script.name} did not answer within {self.timeout:.0f} s")
        reply = proc.stdout.readline()
        if not reply:
            raise CheckerFailure(f"{self.script.name} exited unexpectedly")
        try:
            data = json.loads(reply)
        except json.JSONDecodeError as e:
            raise CheckerFailure(f"{self.script.name} printed to stdout: {reply[:200]!r}") from e
        if "error" in data:
            raise CheckerFailure(f"{self.script.name} failed:\n{data['error'].rstrip()}")
        return bool(data["ok"]), str(data["msg"])

    def close(self) -> None:
        if self._finalizer is not None:
            self._finalizer()
            self._finalizer = None
        self._proc = None
