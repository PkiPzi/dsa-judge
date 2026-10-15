"""CLI behaviour that doesn't need the full variant matrix."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dsajudge.cli import app

from .conftest import FIXTURE_PROBLEM, SOLUTIONS

runner = CliRunner()


def dsa(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.output


def workdir(home: Path, part: int = 1) -> Path:
    return home / "work" / "max-subarray" / f"part{part}"


def test_help_and_version() -> None:
    code, out = dsa("--help")
    assert code == 0
    for cmd in ("doctor", "open", "run", "submit", "debug", "watch", "show", "list", "import"):
        assert cmd in out
    code, out = dsa("--version")
    assert code == 0 and "dsa-judge" in out


def test_doctor_runs(home: Path) -> None:
    code, out = dsa("doctor")
    assert code == 0
    assert "g++" in out and "memory limits" in out and "ollama" in out


def test_list_import_show(home: Path) -> None:
    code, out = dsa("list")
    assert code == 0 and "empty" in out
    code, out = dsa("import", str(FIXTURE_PROBLEM))
    assert code == 0 and "imported" in out
    code, out = dsa("list", "--tag", "dp")
    assert "max-subarray" in out
    code, out = dsa("list", "--tag", "graphs")
    assert "max-subarray" not in out
    code, out = dsa("show", "max-subarray")
    assert code == 0
    assert "brute-only" in out and "locked" in out


def test_errors(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    code, out = dsa("run")
    assert code == 2 and "not inside a work directory" in out
    code, out = dsa("open", "no-such-problem", "--no-editor")
    assert code == 2 and "no problem" in out
    code, out = dsa("open", "max-subarray", "--lang", "rust", "--no-editor")
    assert code == 2 and "unknown language" in out


def test_privacy_note_once(home: Path) -> None:
    _, out = dsa("open", "max-subarray", "--no-editor")
    assert "Privacy" in out
    _, out = dsa("open", "max-subarray", "--no-editor")
    assert "Privacy" not in out


def test_editor_launch(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log = tmp_path / "editor.log"
    script = tmp_path / "fake-editor"
    script.write_text(f'#!/bin/sh\necho "$PWD $@" > {log}\n')
    script.chmod(0o755)
    monkeypatch.setenv("DSA_EDITOR", f"{script} --wait")
    code, _ = dsa("open", "max-subarray")
    assert code == 0
    cwd, *args = log.read_text().split()
    assert cwd == str(workdir(home))
    assert args == ["--wait", str(workdir(home) / "sol.cpp")]


def test_run_case_strict_debug(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dsa("open", "max-subarray", "--no-editor")
    wd = workdir(home)
    shutil.copyfile(SOLUTIONS / "part1" / "wrong.cpp", wd / "sol.cpp")
    monkeypatch.chdir(wd)

    code, out = dsa("run", "--case", "[1,-5,2]")
    assert code == 1
    assert "Wrong Answer" in out and "sample/2" in out
    assert "custom case output: 2" in out

    code, out = dsa("submit", "--strict")
    assert code == 1
    assert "passed 1/3" in out
    assert "expected" not in out  # strict mode hides the failing case

    code, out = dsa("debug")  # defaults to the last failing test
    assert code == 1 and "sample/2" in out

    code, out = dsa("run", "--case", "[1,2]\n3")
    assert code == 2 and "takes 1" in out


def test_watch_reruns_on_save(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dsa("open", "max-subarray", "--no-editor")
    wd = workdir(home)
    shutil.copyfile(SOLUTIONS / "part1" / "correct.cpp", wd / "sol.cpp")
    monkeypatch.chdir(wd)

    def fake_watch(path: Path):  # type: ignore[no-untyped-def]
        yield {(2, str(wd / "statement.md"))}  # ignored: not the solution
        yield {(2, str(wd / "sol.cpp"))}

    monkeypatch.setattr("watchfiles.watch", fake_watch)
    code, out = dsa("watch")
    assert code == 0
    assert out.count("Accepted") == 2
