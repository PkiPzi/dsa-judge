from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from dsajudge.judge import limits

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "fixtures"
FIXTURE_PROBLEM = FIXTURES / "problems" / "max-subarray"
SOLUTIONS = FIXTURES / "solutions" / "max-subarray"


@pytest.fixture(scope="session")
def shared_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One compile/oracle cache per test session: entries are content-addressed."""
    return tmp_path_factory.mktemp("xdg-cache")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shared_cache: Path) -> Iterator[Path]:
    """A temporary DSA_HOME. Tests never touch ~/dsa-bank."""
    bank = tmp_path / "bank"
    monkeypatch.setenv("DSA_HOME", str(bank))
    monkeypatch.setenv("XDG_CACHE_HOME", str(shared_cache))
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.delenv("DSA_EDITOR", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    limits._cache.clear()
    yield bank
    limits._cache.clear()


@pytest.fixture
def fixture_problem(home: Path) -> Path:
    """The max-subarray fixture copied into the temp bank."""
    dest = home / "problems" / "max-subarray"
    shutil.copytree(FIXTURE_PROBLEM, dest)
    return dest


def write_problem(root: Path, data: dict[str, Any], files: dict[str, str] | None = None) -> Path:
    """Write a synthetic problem (problem.yaml + statements + extra files) under root/slug."""
    d = root / data["slug"]
    d.mkdir(parents=True, exist_ok=True)
    for part in data["parts"]:
        stmt = d / part["statement"]
        stmt.parent.mkdir(parents=True, exist_ok=True)
        stmt.write_text(f"# {part['title']}\n", encoding="utf-8")
    for name, content in (files or {}).items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    # JSON is valid YAML 1.2 and avoids any quoting surprises for tricky strings.
    (d / "problem.yaml").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return d


def simple_part(
    pid: int,
    method: str,
    params: list[tuple[str, str]],
    returns: str,
    samples: list[tuple[list[Any], Any]],
    **extra: Any,
) -> dict[str, Any]:
    part: dict[str, Any] = {
        "id": pid,
        "title": f"part {pid}",
        "statement": f"part{pid}/statement.md",
        "signature": {
            "method": method,
            "params": [{"name": n, "type": t} for n, t in params],
            "returns": returns,
        },
        "samples": [{"args": a, "expected": e} for a, e in samples],
        "checker": "exact",
        "limits": {"time_ms": 2000, "memory_mb": 256},
    }
    part.update(extra)
    return part
