"""whisper.cpp through its ``whisper-cli`` binary (no network: it is a local process)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from dsajudge.ai.base import AIError

BIN_NAMES = ("whisper-cli", "whisper-cpp", "whisper.cpp")


def find_binary(configured: str = "") -> str | None:
    if configured:
        return shutil.which(configured)
    for name in BIN_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def find_model(name: str, model_dir: str = "") -> Path | None:
    """ggml-<name>.bin in the configured dir, XDG data dirs, or the distro packages' dirs."""
    if name.endswith(".bin") and Path(name).is_file():
        return Path(name)
    data = Path(os.environ.get("XDG_DATA_HOME") or "~/.local/share").expanduser()
    dirs = [Path(model_dir).expanduser()] if model_dir else []
    dirs += [
        data / "whisper.cpp",
        data / "whisper.cpp" / "models",
        Path("~/whisper.cpp/models").expanduser(),
        Path("/usr/share/whisper.cpp-model-" + name),
        Path("/usr/share/whisper.cpp/models"),
        Path("/usr/share/whisper.cpp"),
    ]
    for d in dirs:
        candidate = d / f"ggml-{name}.bin"
        if candidate.is_file():
            return candidate
    return None


class WhisperCppTranscriber:
    def __init__(
        self,
        model: str = "small",
        *,
        binary: str = "",
        model_dir: str = "",
        language: str = "auto",
        timeout: float = 1800.0,
    ) -> None:
        self.model = model
        self.binary = binary
        self.model_dir = model_dir
        self.language = language
        self.timeout = timeout

    def to_wav(self, audio: Path, out: Path) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise AIError("ffmpeg not found (needed for voice notes): pacman -S ffmpeg")
        run = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(audio),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(out),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if run.returncode != 0:
            raise AIError(f"ffmpeg could not read {audio.name}: {run.stderr.strip()[:300]}")

    def transcribe(self, audio: Path) -> str:
        exe = find_binary(self.binary)
        if not exe:
            raise AIError("whisper-cli not found: yay -S whisper.cpp (or set ai.whisper_bin)")
        model = find_model(self.model, self.model_dir)
        if model is None:
            raise AIError(
                f"whisper model {self.model!r} not found: download ggml-{self.model}.bin "
                "(see LOCAL_TESTING.md) or set ai.whisper_model_dir"
            )
        with tempfile.TemporaryDirectory(prefix="dsa-whisper-") as tmp:
            wav = Path(tmp) / "in.wav"
            self.to_wav(audio, wav)
            out_base = Path(tmp) / "out"
            run = subprocess.run(
                [
                    exe,
                    "-m",
                    str(model),
                    "-f",
                    str(wav),
                    "-l",
                    self.language,
                    "-nt",
                    "-otxt",
                    "-of",
                    str(out_base),
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
            txt = out_base.with_suffix(".txt")
            if run.returncode != 0 or not txt.exists():
                raise AIError(f"whisper-cli failed: {(run.stderr or run.stdout).strip()[-300:]}")
            return " ".join(txt.read_text(encoding="utf-8").split())
