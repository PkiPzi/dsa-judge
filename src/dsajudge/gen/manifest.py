"""The test manifest (PLAN.md §4.1): every generated test with its seed and hashes.

Small tests are stored in the bank (``partN/tests/small/<id>.in.json`` and ``.out.json``).
Large ones are not stored: they are regenerated from ``(slug, part, group, index)`` seeds into
the cache and checked against ``input_sha256``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dsajudge.model.canon import dumps

MANIFEST_VERSION: Literal[1] = 1


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TestEntry(_M):
    __test__ = False

    id: str
    group: str
    index: int
    seed: int
    size: str
    flavor: str
    input_sha256: str
    input_bytes: int
    # sha256 of the canonical expected output; None for run-only tests (brute-only mode).
    expected_sha256: str | None = None
    stored: bool = False
    # Expected output came from the brute-force oracle, so a mismatch is `WA?`.
    unverified: bool = False
    ref_time_ms: float | None = None

    @property
    def run_only(self) -> bool:
        return self.expected_sha256 is None


class SourceInfo(_M):
    file: str
    lang: str
    sha256: str


class StressInfo(_M):
    cases: int
    ref: str
    brute: str


class Manifest(_M):
    version: Literal[1] = MANIFEST_VERSION
    slug: str
    part: int
    mode: Literal["function", "stdio", "ops"]
    oracle: Literal["ref", "brute-only", "promoted"]
    spec_hash: str
    generator: Literal["dsl", "custom"] = "dsl"
    tl_base_ms: float
    tl_source: str
    refs: list[SourceInfo] = Field(default_factory=list)
    brute: SourceInfo | None = None
    stress: StressInfo | None = None
    tests: list[TestEntry] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.tests:
            out[t.group] = out.get(t.group, 0) + 1
        return out


class ManifestError(Exception):
    pass


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def expected_sha(value: Any) -> str:
    """Hash of an expected output: canonical JSON, or the normalized text in stdio mode."""
    return sha256_text(value if isinstance(value, str) else dumps(value))


def part_tests_dir(problem_dir: Path, part_id: int) -> Path:
    return problem_dir / f"part{part_id}" / "tests"


def manifest_path(problem_dir: Path, part_id: int) -> Path:
    return part_tests_dir(problem_dir, part_id) / "manifest.json"


def stored_name(test_id: str) -> str:
    return test_id.replace("/", "-")


def stored_paths(problem_dir: Path, part_id: int, test_id: str) -> tuple[Path, Path]:
    d = part_tests_dir(problem_dir, part_id) / "small"
    n = stored_name(test_id)
    return d / f"{n}.in.json", d / f"{n}.out.json"


def load_manifest(problem_dir: Path, part_id: int) -> Manifest | None:
    path = manifest_path(problem_dir, part_id)
    if not path.is_file():
        return None
    try:
        return Manifest.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ManifestError(f"{path}: unreadable manifest ({e}); run `dsa rebuild`") from e


def save_manifest(problem_dir: Path, manifest: Manifest) -> None:
    path = manifest_path(problem_dir, manifest.part)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(manifest.model_dump_json(indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- disputes


def disputes_path(problem_dir: Path, part_id: int) -> Path:
    return part_tests_dir(problem_dir, part_id) / "disputes.json"


def load_disputes(problem_dir: Path, part_id: int) -> dict[str, str]:
    path = disputes_path(problem_dir, part_id)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data.items()}


def save_disputes(problem_dir: Path, part_id: int, disputes: dict[str, str]) -> None:
    path = disputes_path(problem_dir, part_id)
    if not disputes:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(disputes, indent=1, sort_keys=True) + "\n", encoding="utf-8")
