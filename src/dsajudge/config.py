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


class PlanConfig(_Strict):
    """Test plan (PLAN.md §6.2): how many tests per group and how big "small" is."""

    edge: bool = True
    small_random: int = 40
    medium_random: int = 10
    max: int = 6
    stress_extra: int = 300
    # Largest array length / node count in `small` and `medium` tests; 2-D dimensions
    # (rows, cols) use the *_2d caps. `small` must stay brute-friendly.
    small_cap: int = 8
    small_cap_2d: int = 5
    medium_cap: int = 100
    medium_cap_2d: int = 20
    # Brute-force oracles only run on inputs this small (serialized bytes).
    brute_max_input_bytes: int = 20_000
    brute_timeout_s: float = 10.0
    # Longest the weak-test check lets the brute run on a max test.
    weak_check_cap_s: float = 20.0
    # Tests with inputs up to this size are stored in the bank; bigger ones are regenerated.
    store_max_bytes: int = 64 * 1024


class ModelsConfig(_Strict):
    text: str = "qwen2.5-coder:3b"
    vision: str = "qwen2.5vl:3b"
    whisper: str = "small"


class AIConfig(_Strict):
    ollama_url: str = "http://127.0.0.1:11434"
    whisper_bin: str = ""
    # Directory holding ggml-<model>.bin files; empty searches the usual places.
    whisper_model_dir: str = ""
    # Spoken language for whisper.cpp: "auto", "en", "hi", ...
    whisper_language: str = "auto"
    # Greedy decoding (1) is ~4x faster than whisper.cpp's default beam of 5 on a GTX 1650.
    whisper_beam_size: int = Field(default=1, ge=1, le=8)
    # Romanized sample text: makes -l hi write Hinglish in Latin script instead of Devanagari.
    # Empty disables it.
    whisper_prompt: str = (
        "Aaj main binary search ka problem solve kar raha hoon, "
        "isme time complexity O(log n) hai, aur array sorted hona chahiye."
    )
    timeout_s: float = 900.0
    num_ctx: int = 8192
    # Every AI-written artifact is verified by execution and retried with the error fed back.
    max_attempts: int = 3
    # Photos larger than this (longest side, px) are downscaled before transcription.
    max_image_side: int = 1600
    models: ModelsConfig = Field(default_factory=ModelsConfig)


class Config(_Strict):
    default_lang: Literal["cpp", "java", "python"] = "cpp"
    # Command used in generated editor tasks; set to an absolute path if `dsa` is not on PATH.
    dsa_command: str = "dsa"
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    plan: PlanConfig = Field(default_factory=PlanConfig)
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
