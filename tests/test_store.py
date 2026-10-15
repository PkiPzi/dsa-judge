from __future__ import annotations

import json
from pathlib import Path

import pytest

from dsajudge.model.loader import ProblemError, load_problem
from dsajudge.store.bank import find_problem, import_problem, list_slugs
from dsajudge.store.db import DB, Attempt
from dsajudge.store.workdir import find_workdir, open_part, tasks_json

from .conftest import FIXTURE_PROBLEM


def _attempt(part: int, verdict: str, kind: str = "submit") -> Attempt:
    return Attempt("max-subarray", part, "cpp", kind, verdict, 1, 1, 5.0, 1000)


def test_db_progress(tmp_path: Path) -> None:
    with DB(tmp_path / "db.sqlite") as db:
        assert db.unlocked_part("max-subarray", 3) == 1
        db.log_attempt(_attempt(1, "AC", kind="run"))  # runs never unlock
        assert db.unlocked_part("max-subarray", 3) == 1
        db.log_attempt(_attempt(1, "WA"))
        assert db.unlocked_part("max-subarray", 3) == 1
        db.log_attempt(_attempt(1, "AC"))
        assert db.unlocked_part("max-subarray", 3) == 2
        assert db.solved_parts("max-subarray") == {1}
        assert db.best_times("max-subarray") == {1: ("cpp", 5.0)}
        db.log_attempt(_attempt(2, "AC"))
        db.log_attempt(_attempt(3, "AC"))
        assert db.unlocked_part("max-subarray", 3) == 3


def test_db_sessions_meta_and_reopen(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    with DB(path) as db:
        assert db.session_started("s", 1) is None
        db.start_session("s", 1, "cpp")
        assert db.session_started("s", 1) is not None
        db.set_meta("k", "v")
        db.log_attempt(Attempt("s", 1, "cpp", "run", "WA", 0, 3, 1, 1, failed_test="sample/2"))
    with DB(path) as db:  # migrations are idempotent
        assert db.get_meta("k") == "v"
        assert db.last_failed_test("s", 1) == "sample/2"
        assert int(db.conn.execute("PRAGMA user_version").fetchone()[0]) == 1


def test_bank_import_and_seed(home: Path) -> None:
    assert list_slugs() == []
    path, seeded = find_problem("max-subarray")
    assert seeded and (path / "problem.yaml").is_file()
    assert find_problem("max-subarray") == (path, False)
    assert list_slugs() == ["max-subarray"]
    with pytest.raises(ProblemError, match="already exists"):
        import_problem(FIXTURE_PROBLEM)
    import_problem(FIXTURE_PROBLEM, force=True)
    with pytest.raises(ProblemError, match="no problem"):
        find_problem("nope")


def test_open_part_layout(fixture_problem: Path) -> None:
    problem = load_problem(fixture_problem)
    info = open_part(fixture_problem, problem, 1, "cpp")
    wd = info.dir
    assert wd == fixture_problem.parent.parent / "work" / "max-subarray" / "part1"
    assert (wd / "sol.cpp").read_text().count("maxSubarraySum(vector<int>& nums)") == 1
    assert (wd / "statement.md").read_text().startswith("# Maximum subarray sum")
    meta = json.loads((wd / ".dsa.json").read_text())
    assert meta == {"slug": "max-subarray", "part": 1, "lang": "cpp", "bank": str(fixture_problem)}
    tasks = json.loads((wd / ".vscode" / "tasks.json").read_text())
    assert [t["label"] for t in tasks["tasks"]][:2] == ["dsa: run", "dsa: submit"]

    # never overwrite the user's code
    (wd / "sol.cpp").write_text("// mine\n")
    open_part(fixture_problem, problem, 1, "cpp")
    assert (wd / "sol.cpp").read_text() == "// mine\n"

    # a second language adds its own file
    open_part(fixture_problem, problem, 1, "python")
    assert (wd / "sol.py").exists() and (wd / "sol.cpp").exists()

    # prev.<ext> for follow-ups
    info2 = open_part(fixture_problem, problem, 2, "cpp")
    assert (info2.dir / "prev.cpp").read_text() == "// mine\n"
    assert not (info2.dir / "prev.py").exists()

    found = find_workdir(wd / ".vscode")
    assert found is not None and found.part == 1 and found.lang == "python"
    assert find_workdir(fixture_problem) is None


def test_tasks_json_command() -> None:
    data = json.loads(tasks_json("/opt/bin/dsa"))
    assert data["tasks"][0]["command"] == "/opt/bin/dsa run"
    assert data["tasks"][0]["group"] == {"kind": "build", "isDefault": True}
