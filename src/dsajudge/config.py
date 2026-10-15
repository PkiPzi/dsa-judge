"""Configuration: DSA_HOME resolution, config.toml, XDG cache dir."""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_HOME = "~/dsa-bank"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JudgeConfig(_Strict):
    # Practice-language time multipliers relative to the base (C++) limit.
    time_multipliers: dict[str, float] = Field(
        default_factory=lambda: {"cpp": 1.0, "java": 2.0, "python": 5.0}
    )
    memory_backend: Literal["auto", "systemd", "poll"] = "auto"
    cxx: str = "g++"
    cxx_flags: list[str] = Field(default_factory=lambda: ["-std=c++20", "-O2", "-pipe"])
    javac: str = "javac"
    java: str = "java"
    # Interpreter for Python solutions; empty means the judge's own interpreter.
    python: str = ""
    ref_timeout_s: float = 60.0
    use_pch: bool = True

    def python_exe(self) -> str:
        return self.python or sys.executable


class ModelsConfig(_Strict):
    text: str = "qwen2.5-coder:3b"
    vision: str = "qwen2.5vl:3b"
    whisper: str = "small"


class AIConfig(_Strict):
    ollama_url: str = "http://127.0.0.1:11434"
    whisper_bin: str = ""
    models: ModelsConfig = Field(default_factory=ModelsConfig)


class Config(_Strict):
    default_lang: Literal["cpp", "java", "python"] = "cpp"
    # Command used in generated editor tasks; set to an absolute path if `dsa` is not on PATH.
    dsa_command: str = "dsa"
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    ai: AIConfig = Field(default_factory=AIConfig)


def dsa_home() -> Path:
    """The problem bank root: $DSA_HOME, default ~/dsa-bank."""
    raw = os.environ.get("DSA_HOME") or DEFAULT_HOME
    return Path(raw).expanduser().resolve()


def cache_dir() -> Path:
    """$XDG_CACHE_HOME/dsajudge (default ~/.cache/dsajudge)."""
    base = os.environ.get("XDG_CACHE_HOME") or "~/.cache"
    return (Path(base).expanduser() / "dsajudge").resolve()


def config_path() -> Path:
    return dsa_home() / "config.toml"


class ConfigError(Exception):
    pass


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    try:
        return Config.model_validate(data)
    except ValueError as e:
        raise ConfigError(f"{path}: {e}") from e
