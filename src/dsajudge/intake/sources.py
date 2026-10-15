"""Collect stage (PLAN.md §7.1): classify inputs, order them, keep originals forever."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
AUDIO_EXT = {".m4a", ".mp3", ".ogg", ".opus", ".wav"}
TEXT_EXT = {".txt", ".md"}
CODE_EXT = {".cpp": "cpp", ".cc": "cpp", ".java": "java", ".py": "python"}

Kind = Literal["image", "audio", "text", "code"]


class IntakeError(Exception):
    """Intake cannot continue; the message says what to do."""


class SourceRec(BaseModel):
    n: int  # 1-based position, also the transcript section number
    name: str  # file name inside raw/
    original: str  # the name the user gave
    kind: Kind
    lang: str | None = None
    sha256: str


@dataclass(frozen=True)
class Classified:
    path: Path
    kind: Kind
    lang: str | None = None


def classify(path: Path) -> Classified:
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        return Classified(path, "image")
    if ext in AUDIO_EXT:
        return Classified(path, "audio")
    if ext in TEXT_EXT:
        return Classified(path, "text")
    if ext in CODE_EXT:
        return Classified(path, "code", CODE_EXT[ext])
    allowed = sorted(IMAGE_EXT | AUDIO_EXT | TEXT_EXT | set(CODE_EXT))
    raise IntakeError(
        f"{path.name}: unsupported file type {ext or '(none)'}; use {' '.join(allowed)}"
    )


def natural_key(name: str) -> list[object]:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def exif_or_mtime(path: Path) -> float:
    """EXIF capture time for photos that have it, else the file's mtime."""
    if path.suffix.lower() in IMAGE_EXT:
        try:
            from PIL import Image

            with Image.open(path) as im:
                raw = im.getexif().get(36867) or im.getexif().get(306)  # DateTimeOriginal/DateTime
            if raw:
                import time

                return time.mktime(time.strptime(str(raw), "%Y:%m:%d %H:%M:%S"))
        except Exception:
            pass
    return path.stat().st_mtime


def order_sources(paths: list[Path]) -> list[Path]:
    """Ordered by file name (numbers compared as numbers), then EXIF/mtime."""
    return sorted(paths, key=lambda p: (natural_key(p.name), exif_or_mtime(p)))


def read_clipboard() -> str | None:
    """Clipboard text via wl-paste (Wayland) or xclip."""
    for cmd in (["wl-paste", "--no-newline"], ["xclip", "-o", "-selection", "clipboard"]):
        if not shutil.which(cmd[0]):
            continue
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
        if run.returncode == 0 and run.stdout.strip():
            return run.stdout
    return None


def pick_files() -> list[Path]:
    """Let the user multi-select inputs in KDE's file dialog (kdialog); order is fixed later."""
    kdialog = shutil.which("kdialog")
    if not kdialog:
        raise IntakeError("no files given and kdialog is missing: sudo pacman -S kdialog")
    exts = sorted(IMAGE_EXT | AUDIO_EXT | TEXT_EXT | set(CODE_EXT))
    start = next(
        (d for d in (Path.home() / "Pictures", Path.home() / "Downloads") if d.is_dir()),
        Path.home(),
    )
    run = subprocess.run(
        [
            kdialog,
            "--title",
            "dsa add: pick photos, voice notes, code (Ctrl/Shift-click for several)",
            "--multiple",
            "--separate-output",
            "--getopenfilename",
            str(start),
            " ".join(f"*{e}" for e in exts) + "|Question files\n*|All files",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    picked = [Path(line) for line in run.stdout.splitlines() if line.strip()]
    if run.returncode != 0 or not picked:
        raise IntakeError("nothing was picked")
    return picked


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect(raw_dir: Path, paths: list[Path], texts: list[tuple[str, str]]) -> list[SourceRec]:
    """Copy inputs into ``raw_dir`` as ``NN-name.ext`` (kept forever) and describe them.

    ``texts`` are (name, content) pairs for clipboard / typed input; they come after files.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    recs: list[SourceRec] = []
    classified = [classify(p) for p in order_sources(paths)]
    for c in classified:
        if not c.path.is_file():
            raise IntakeError(f"{c.path} does not exist")
    n = 0
    for c in classified:
        n += 1
        dest = raw_dir / f"{n:02d}-{c.path.name}"
        shutil.copyfile(c.path, dest)
        recs.append(
            SourceRec(
                n=n,
                name=dest.name,
                original=c.path.name,
                kind=c.kind,
                lang=c.lang,
                sha256=sha256_file(dest),
            )
        )
    for name, content in texts:
        n += 1
        dest = raw_dir / f"{n:02d}-{name}"
        dest.write_text(content, encoding="utf-8")
        recs.append(
            SourceRec(n=n, name=dest.name, original=name, kind="text", sha256=sha256_file(dest))
        )
    if not recs:
        raise IntakeError("nothing to add: give files, --clip or --text")
    return recs
