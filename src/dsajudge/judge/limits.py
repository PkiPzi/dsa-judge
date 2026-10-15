"""Resource limits: memory backend (systemd scope or RSS polling) and stack size.

RLIMIT_AS is never used: it breaks the JVM and AddressSanitizer.
"""

from __future__ import annotations

import os
import resource
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Literal

BackendKind = Literal["systemd", "poll"]


@dataclass(frozen=True)
class MemoryBackend:
    kind: BackendKind
    detail: str


_cache: dict[str, MemoryBackend] = {}


def probe_systemd() -> tuple[bool, str]:
    exe = shutil.which("systemd-run")
    if not exe:
        return False, "systemd-run not found"
    try:
        out = subprocess.run(
            [
                exe,
                "--user",
                "--scope",
                "--quiet",
                "-p",
                "MemoryMax=64M",
                "-p",
                "MemorySwapMax=0",
                "true",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"systemd-run failed: {e}"
    if out.returncode != 0:
        msg = (out.stderr or out.stdout).strip().splitlines()
        return False, f"systemd-run --user unavailable ({msg[0] if msg else out.returncode})"
    return True, "systemd-run --user --scope with MemoryMax"


def detect_backend(preference: str = "auto") -> MemoryBackend:
    if preference in _cache:
        return _cache[preference]
    if preference == "poll":
        backend = MemoryBackend("poll", "RSS polling (forced by config)")
    else:
        ok, detail = probe_systemd()
        if ok:
            backend = MemoryBackend("systemd", detail)
        elif preference == "systemd":
            backend = MemoryBackend("poll", f"RSS polling; systemd requested but {detail}")
        else:
            backend = MemoryBackend("poll", f"RSS polling ({detail})")
    _cache[preference] = backend
    return backend


def wrap_command(argv: list[str], backend: MemoryBackend, limit_mb: int | None) -> list[str]:
    if backend.kind != "systemd" or limit_mb is None:
        return argv
    return [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "--collect",
        "-p",
        f"MemoryMax={limit_mb}M",
        "-p",
        "MemorySwapMax=0",
        "--",
        *argv,
    ]


_stack_raised = False


def raise_stack_limit() -> str:
    """Raise RLIMIT_STACK in this process so every child inherits a big main-thread stack.

    Set in the parent rather than in preexec_fn, which is unsafe with threads.
    """
    global _stack_raised
    soft, hard = resource.getrlimit(resource.RLIMIT_STACK)
    if not _stack_raised:
        try:
            resource.setrlimit(resource.RLIMIT_STACK, (hard, hard))
        except (ValueError, OSError):
            pass
        _stack_raised = True
        soft, hard = resource.getrlimit(resource.RLIMIT_STACK)
    return "unlimited" if soft == resource.RLIM_INFINITY else f"{soft // (1024 * 1024)} MB"


def read_rss_kb(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/status", encoding="ascii", errors="replace") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return 0


class RssWatchdog(threading.Thread):
    """Polls a process's resident memory; kills its process group above ``limit_kb``."""

    def __init__(self, pid: int, limit_kb: int | None, interval: float = 0.01) -> None:
        super().__init__(daemon=True)
        self.pid = pid
        self.limit_kb = limit_kb
        self.interval = interval
        self.peak_kb = 0
        self.killed = False
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            rss = read_rss_kb(self.pid)
            if rss is None:
                return
            self.peak_kb = max(self.peak_kb, rss)
            if self.limit_kb is not None and rss > self.limit_kb:
                self.killed = True
                kill_group(self.pid)
                return
            time.sleep(self.interval)

    def stop(self) -> None:
        self._stop_event.set()


def kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
