"""Shell completion: the generated scripts and the dynamic answers (slugs, langs, test ids)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dsajudge.cli import app

from .conftest import FIXTURE_PROBLEM

runner = CliRunner()
DSA = Path(sys.executable).parent / "dsa"


@pytest.mark.parametrize("shell", ["zsh", "bash", "fish"])
def test_scripts_call_back_into_dsa(shell: str) -> None:
    res = runner.invoke(app, ["completions", shell])
    assert res.exit_code == 0, res.output
    assert "_DSA_COMPLETE" in res.output and "dsa" in res.output


def test_unknown_shell_and_default_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    res = runner.invoke(app, ["completions", "tcsh"])
    assert res.exit_code == 2 and "zsh, bash, fish" in res.output
    monkeypatch.setenv("SHELL", "/usr/bin/zsh")
    res = runner.invoke(app, ["completions"])
    assert res.exit_code == 0 and "#compdef dsa" in res.output


def test_install_writes_xdg_files_and_leaves_rc_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    res = runner.invoke(app, ["completions", "zsh", "--install"])
    assert res.exit_code == 0
    target = tmp_path / "data" / "zsh" / "site-functions" / "_dsa"
    assert target.read_text().startswith("#compdef dsa")
    assert "fpath=(" in res.output and str(target.parent) in res.output
    runner.invoke(app, ["completions", "bash", "--install"])
    runner.invoke(app, ["completions", "fish", "--install"])
    assert (tmp_path / "data" / "bash-completion" / "completions" / "dsa").is_file()
    assert (tmp_path / "config" / "fish" / "completions" / "dsa.fish").is_file()
    assert not (tmp_path / "home").exists()  # no ~/.zshrc or ~/.zfunc


def complete(words: list[str], cword: int, cwd: Path | None = None) -> list[str]:
    env = {
        **os.environ,
        "_DSA_COMPLETE": "complete_bash",
        "COMP_WORDS": " ".join(words),
        "COMP_CWORD": str(cword),
    }
    out = subprocess.run([str(DSA)], env=env, cwd=cwd, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


@pytest.mark.skipif(not DSA.exists(), reason="needs the installed `dsa` script")
def test_dynamic_completion(home: Path) -> None:
    shutil.copytree(FIXTURE_PROBLEM, home / "problems" / "max-subarray")
    shutil.copytree(FIXTURE_PROBLEM, home / "problems" / "other-one")
    assert complete(["dsa", "open", ""], 2) == ["max-subarray", "other-one"]
    assert complete(["dsa", "open", "max"], 2) == ["max-subarray"]
    assert complete(["dsa", "review", "start", "o"], 3) == ["other-one"]
    assert complete(["dsa", "run", "--lang", "p"], 3) == ["python"]
    assert complete(["dsa", "stats", "--tag", "d"], 3) == ["dp"]
    assert "completions" in complete(["dsa", "comp"], 1)

    # test ids come from the work dir's part: samples first, then the built tests
    runner.invoke(app, ["open", "max-subarray", "--no-editor"])
    wd = home / "work" / "max-subarray" / "part1"
    assert complete(["dsa", "debug", "sam"], 2, cwd=wd) == ["sample/1", "sample/2", "sample/3"]
    assert complete(["dsa", "debug", ""], 2, cwd=home) == []  # outside a work dir: nothing
