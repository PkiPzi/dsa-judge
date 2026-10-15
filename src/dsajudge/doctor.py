"""`dsa doctor`: toolchain and environment checks. Missing items are warnings, not failures."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Literal

from dsajudge.ai.net import NonLoopbackError
from dsajudge.ai.ollama import ping
from dsajudge.config import Config, cache_dir, dsa_home
from dsajudge.judge.limits import detect_backend, raise_stack_limit

WHISPER_NAMES = ("whisper-cli", "whisper-cpp", "whisper.cpp")


@dataclass
class Check:
    name: str
    status: Literal["ok", "warn"]
    detail: str
    hint: str = ""


def _version(exe: str, *args: str) -> str:
    try:
        out = subprocess.run([exe, *args], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "?"
    text = (out.stdout + "\n" + out.stderr).strip()
    for line in text.splitlines():
        if line.strip() and not line.startswith("Picked up"):
            return line.strip()
    return "?"


def _tool(name: str, exe: str, version_args: tuple[str, ...], purpose: str, hint: str) -> Check:
    path = shutil.which(exe)
    if not path:
        return Check(name, "warn", f"{exe} not found ({purpose})", hint)
    return Check(name, "ok", f"{_version(path, *version_args)}  [{path}]")


def run_checks(cfg: Config) -> list[Check]:
    checks: list[Check] = []

    home = dsa_home()
    if home.exists():
        writable = os.access(home, os.W_OK)
        checks.append(
            Check(
                "DSA_HOME",
                "ok" if writable else "warn",
                f"{home}" + ("" if writable else " (not writable)"),
            )
        )
    else:
        checks.append(Check("DSA_HOME", "ok", f"{home} (will be created on first use)"))
    checks.append(Check("cache", "ok", str(cache_dir())))

    checks.append(
        _tool("g++", cfg.judge.cxx, ("--version",), "C++ practice and refs", "pacman -S gcc")
    )
    checks.append(
        _tool(
            "javac",
            cfg.judge.javac,
            ("-version",),
            "Java practice and refs",
            "pacman -S jdk-openjdk",
        )
    )
    checks.append(
        _tool(
            "java", cfg.judge.java, ("-version",), "Java practice and refs", "pacman -S jdk-openjdk"
        )
    )
    py = cfg.judge.python_exe()
    checks.append(Check("python", "ok", f"{_version(py, '--version')}  [{py}]"))
    checks.append(_tool("python3", "python3", ("--version",), "system Python", "pacman -S python"))
    checks.append(
        _tool("ffmpeg", "ffmpeg", ("-version",), "voice notes, Phase 3", "pacman -S ffmpeg")
    )

    whisper = cfg.ai.whisper_bin or next((n for n in WHISPER_NAMES if shutil.which(n)), "")
    if whisper and shutil.which(whisper):
        checks.append(Check("whisper.cpp", "ok", str(shutil.which(whisper))))
    else:
        checks.append(
            Check(
                "whisper.cpp",
                "warn",
                "whisper-cli not found (voice notes, Phase 3)",
                "yay -S whisper.cpp",
            )
        )

    try:
        version = ping(cfg.ai.ollama_url)
    except NonLoopbackError as e:
        checks.append(
            Check("ollama", "warn", str(e), "set ai.ollama_url to http://127.0.0.1:11434")
        )
    else:
        if version:
            checks.append(Check("ollama", "ok", f"{version} at {cfg.ai.ollama_url}"))
        else:
            checks.append(
                Check(
                    "ollama",
                    "warn",
                    f"not reachable at {cfg.ai.ollama_url} (photo intake, Phase 3)",
                    "pacman -S ollama-cuda && systemctl enable --now ollama",
                )
            )

    backend = detect_backend(cfg.judge.memory_backend)
    checks.append(
        Check(
            "memory limits",
            "ok",
            f"{backend.kind}: {backend.detail}",
        )
    )
    checks.append(Check("stack limit", "ok", f"RLIMIT_STACK raised to {raise_stack_limit()}"))
    return checks
