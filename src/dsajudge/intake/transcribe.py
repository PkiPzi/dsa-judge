"""Transcribe stage (PLAN.md §7.1): photos -> vision model, voice -> whisper.cpp, text as-is."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from dsajudge.ai.base import LLM, Transcriber
from dsajudge.ai.prompts import load_prompt
from dsajudge.intake.sources import IntakeError, SourceRec

FENCE = re.compile(r"```[ \t]*([\w+#-]*)[ \t]*\n(.*?)\n?```", re.DOTALL)
UNREADABLE = "[??]"


def prepare_image(src: Path, out: Path, max_side: int) -> Path:
    """HEIC and large photos become a downscaled PNG (EXIF rotation applied)."""
    try:
        from PIL import Image, ImageOps
    except ImportError as e:  # pragma: no cover
        raise IntakeError("Pillow is missing: run `uv sync --all-extras`") from e
    if src.suffix.lower() == ".heic":
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
        except ImportError as e:  # pragma: no cover
            raise IntakeError("pillow-heif is missing: run `uv sync --all-extras`") from e
    with Image.open(src) as opened:
        img: Image.Image = ImageOps.exif_transpose(opened) or opened
        if max(img.size) > max_side:
            scale = max_side / max(img.size)
            size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
            img = img.resize(size, Image.Resampling.LANCZOS)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, format="PNG")
    return out


def transcribe_one(
    rec: SourceRec,
    raw_dir: Path,
    work_dir: Path,
    vision: LLM,
    audio: Transcriber,
    max_side: int,
) -> str:
    path = raw_dir / rec.name
    if rec.kind == "image":
        png = prepare_image(path, work_dir / (Path(rec.name).stem + ".png"), max_side)
        system, user = load_prompt("transcribe_image").render()
        return vision.complete(user, system=system, images=[png]).strip()
    if rec.kind == "audio":
        return audio.transcribe(path).strip()
    text = path.read_text(encoding="utf-8")
    if rec.kind == "code":
        return f"```{rec.lang}\n{text.rstrip(chr(10))}\n```"
    return text.strip()


def section_header(rec: SourceRec) -> str:
    extra = f", {rec.lang}" if rec.lang else ""
    return f"## [{rec.n}] {rec.original} ({rec.kind}{extra})"


def assemble(recs: list[SourceRec], sections: dict[int, str]) -> str:
    return "\n\n".join(f"{section_header(r)}\n\n{sections[r.n]}" for r in recs) + "\n"


def parse_sections(transcript: str) -> dict[int, str]:
    """Section number -> body, from an assembled transcript."""
    out: dict[int, str] = {}
    parts = re.split(r"^## \[(\d+)\] .*$", transcript, flags=re.MULTILINE)
    for i in range(1, len(parts), 2):
        out[int(parts[i])] = parts[i + 1].strip("\n")
    return out


def code_blocks(section: str) -> list[tuple[str, str]]:
    """(language tag, code) of each fenced block of a section; indentation is kept exactly."""
    return [(m.group(1).lower(), m.group(2)) for m in FENCE.finditer(section)]


def unresolved(text: str) -> int:
    return text.count(UNREADABLE)


ProgressFn = Callable[[str], None]
