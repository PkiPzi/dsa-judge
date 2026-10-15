"""`dsa doctor --models`: run each configured model on fixture inputs and report how often the
answer is usable (schema-valid and sane) and how long it takes."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from dsajudge.ai.base import AIError
from dsajudge.ai.factory import ProviderFactory
from dsajudge.ai.prompts import load_prompt
from dsajudge.ai.retry import extract_json
from dsajudge.ai.schemas import ConstraintsOut, Extraction, Segmentation, json_schema
from dsajudge.ai.synth import render_text_image
from dsajudge.config import Config
from dsajudge.gen.validator import validate_args
from dsajudge.intake.structure import part_model, signature_line
from dsajudge.model.constraints import parse_constraints

DATA = Path(__file__).parent / "bench_data"
STATEMENT_LINES = [
    "Sum of two numbers",
    "Given an array nums of integers and an integer target, return the indices",
    "of two different elements whose sum equals target.",
    "Constraints:  2 <= nums.length <= 10^{5}   and   -10^{9} <= nums[i] <= 10^{9}",
    "Example 1:  Input: nums = [2, 7, 11, 15], target = 9   Output: [0, 1]",
    "    def twoSum(self, nums, target):",
    "        return []",
]
VISION_EXPECT = ("10^5", "nums", "target", "[0, 1]")


@dataclass
class BenchRow:
    role: str
    model: str
    ok: int = 0
    runs: int = 0
    seconds: list[float] = field(default_factory=list)
    note: str = ""

    @property
    def latency(self) -> float:
        return sum(self.seconds) / len(self.seconds) if self.seconds else 0.0


def _timed(row: BenchRow, fn: Callable[[], bool]) -> None:
    t0 = time.monotonic()
    row.runs += 1
    try:
        good = fn()
    except AIError as e:
        row.note = str(e)[:160]
        return
    except Exception as e:
        row.note = f"{type(e).__name__}: {e}"[:160]
        return
    row.seconds.append(time.monotonic() - t0)
    row.ok += bool(good)


def bench_text(cfg: Config, providers: ProviderFactory, runs: int) -> list[BenchRow]:
    llm = providers.text(cfg)
    rows = [
        BenchRow(f"text:{t}", cfg.ai.models.text) for t in ("segment", "extract", "constraints")
    ]
    transcript = (DATA / "transcript.md").read_text(encoding="utf-8")
    statement = (DATA / "statement.md").read_text(encoding="utf-8")

    def segment() -> bool:
        system, user = load_prompt("segment").render(transcript=transcript)
        seg = Segmentation.model_validate_json(
            extract_json(llm.complete(user, system=system, schema=json_schema(Segmentation)))
        )
        return seg.parts[0].part == 1

    ext_holder: list[Extraction] = []

    def extract() -> bool:
        system, user = load_prompt("extract_part").render(part="1", text=statement, context="")
        ext = Extraction.model_validate_json(
            extract_json(llm.complete(user, system=system, schema=json_schema(Extraction)))
        )
        part_model(ext, {}, 1)  # types and samples must be usable
        ext_holder[:] = [ext]
        return len(ext.params) == 2 and len(ext.samples) >= 1

    def constraints() -> bool:
        ext = (
            ext_holder[0]
            if ext_holder
            else Extraction.model_validate(
                {
                    "title": "t",
                    "statement": "s",
                    "method": "twoSum",
                    "returns": "int[]",
                    "params": [
                        {"name": "nums", "type": "int[]"},
                        {"name": "target", "type": "int"},
                    ],
                    "constraints_text": [
                        "2 <= nums.length <= 10^5",
                        "-10^9 <= nums[i] <= 10^9",
                        "-10^9 <= target <= 10^9",
                    ],
                    "samples": [{"args": [[2, 7, 11, 15], 9], "expected": [0, 1]}],
                }
            )
        )
        system, user = load_prompt("fill_constraints").render(
            signature=signature_line(ext), constraints="\n".join(ext.constraints_text), feedback=""
        )
        out = ConstraintsOut.model_validate_json(
            extract_json(llm.complete(user, system=system, schema=json_schema(ConstraintsOut)))
        )
        part = part_model(ext, out.constraints, 1)
        specs = parse_constraints(part)
        return all(not validate_args(specs, s.args) for s in part.samples)

    for _ in range(runs):
        _timed(rows[0], segment)
        _timed(rows[1], extract)
        _timed(rows[2], constraints)
    return rows


def bench_vision(cfg: Config, providers: ProviderFactory, runs: int) -> list[BenchRow]:
    row = BenchRow("vision:ocr", cfg.ai.models.vision)
    llm = providers.vision(cfg)
    with tempfile.TemporaryDirectory(prefix="dsa-bench-") as tmp:
        img = render_text_image(STATEMENT_LINES, Path(tmp) / "statement.png")
        system, user = load_prompt("transcribe_image").render()

        def ocr() -> bool:
            text = llm.complete(user, system=system, images=[img])
            missing = [t for t in VISION_EXPECT if t not in text]
            if missing:
                row.note = f"missing in the transcript: {', '.join(missing)}"
            return not missing

        for _ in range(runs):
            _timed(row, ocr)
    return [row]


def bench_whisper(cfg: Config, providers: ProviderFactory) -> list[BenchRow]:
    row = BenchRow("whisper:runs", cfg.ai.models.whisper)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        row.note = "ffmpeg not found"
        return [row]
    t = providers.transcriber(cfg)
    with tempfile.TemporaryDirectory(prefix="dsa-bench-") as tmp:
        wav = Path(tmp) / "silence.wav"
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=16000:cl=mono",
                "-t",
                "2",
                str(wav),
            ],
            check=True,
        )
        _timed(row, lambda: isinstance(t.transcribe(wav), str))
    row.note = row.note or "loads and runs (accuracy is not measured: no speech sample here)"
    return [row]


def run_bench(cfg: Config, providers: ProviderFactory, runs: int = 3) -> list[BenchRow]:
    return [
        *bench_vision(cfg, providers, runs),
        *bench_text(cfg, providers, runs),
        *bench_whisper(cfg, providers),
    ]
