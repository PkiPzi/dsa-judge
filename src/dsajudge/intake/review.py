"""Review stage (PLAN.md §7.1 stage 4): mandatory, in $EDITOR, repeated until valid."""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

from dsajudge.intake.draft import draft_files, examine, refresh_header
from dsajudge.intake.sources import IntakeError

Editor = Callable[[list[Path]], int]


class ReviewAborted(IntakeError):
    pass


def editor_command() -> list[str]:
    raw = os.environ.get("DSA_EDITOR") or os.environ.get("EDITOR")
    if not raw:
        raise IntakeError(
            "set $EDITOR (or $DSA_EDITOR): reviewing the transcription is mandatory, "
            "nothing is built from an unreviewed draft"
        )
    argv = shlex.split(raw)
    # GUI editors return immediately unless told to wait.
    if Path(argv[0]).name in ("code", "codium", "code-oss") and "--wait" not in argv:
        argv.insert(1, "--wait")
    return argv


def run_editor(files: list[Path]) -> int:
    argv = [*editor_command(), *map(str, files)]
    return subprocess.run(argv, check=False).returncode


def review_loop(
    problem_dir: Path,
    edit: Editor = run_editor,
    *,
    first_new_part: int = 1,
    notes: list[str] | None = None,
    say: Callable[[str], None] = lambda _m: None,
) -> None:
    """Open the draft until it validates. Emptying problem.yaml or a failing editor aborts."""
    while True:
        refresh_header(problem_dir, first_new_part, notes)
        _, problem = examine(problem_dir, first_new_part)
        files = [problem_dir / "problem.yaml", *draft_files(problem_dir, problem, first_new_part)]
        if edit(files) != 0:
            raise ReviewAborted("the editor exited with an error; the draft is kept for --resume")
        path = problem_dir / "problem.yaml"
        if not path.exists() or not path.read_text(encoding="utf-8").strip():
            raise ReviewAborted("problem.yaml was emptied: review aborted, the draft is kept")
        findings, _ = examine(problem_dir, first_new_part)
        if not findings.errors:
            say("review accepted")
            return
        say(f"the draft still has {len(findings.errors)} problem(s); opening it again")
