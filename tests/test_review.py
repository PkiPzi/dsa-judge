"""Spaced repetition: the schedule (injected clocks) and the `dsa review` flow."""

from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dsajudge import review
from dsajudge.cli import app
from dsajudge.store.db import DB, Attempt

from .conftest import SOLUTIONS

DAY = review.DAY
T0 = 1_700_000_000.0


def sub(db: DB, verdict: str, at: float, slug: str = "p", part: int = 1) -> None:
    db.log_attempt(Attempt(slug, part, "cpp", "submit", verdict, 1, 1, 5.0, 100, created_at=at))


def item(db: DB, now: float, part: int = 1) -> review.ReviewItem:
    it = review.item_for(db, "p", part, now)
    assert it is not None
    return it


def test_never_submitted_has_no_item(tmp_path: Path) -> None:
    with DB(tmp_path / "db") as db:
        assert review.item_for(db, "p", 1, T0) is None
        db.log_attempt(Attempt("p", 1, "cpp", "run", "WA", 0, 1, 1.0, 1, created_at=T0))
        assert review.item_for(db, "p", 1, T0) is None  # runs don't count


def test_first_review_is_due_after_three_days(tmp_path: Path) -> None:
    with DB(tmp_path / "db") as db:
        sub(db, "AC", T0)
        it = item(db, T0 + 2 * DAY)
        assert (it.state, it.reason, it.stage) == ("upcoming", "review", 0)
        assert it.due_at == T0 + 3 * DAY
        assert item(db, T0 + 3 * DAY).state == "due"
        assert item(db, T0 + 10 * DAY).state == "due"


def test_failed_part_comes_back_after_one_day(tmp_path: Path) -> None:
    with DB(tmp_path / "db") as db:
        sub(db, "WA", T0)
        sub(db, "TLE", T0 + 100)
        it = item(db, T0 + 100 + 0.5 * DAY)
        assert (it.state, it.reason) == ("upcoming", "unfinished")
        it = item(db, T0 + 100 + DAY)
        assert (it.state, it.reason) == ("due", "unfinished")
        sub(db, "AC", T0 + 2 * DAY)  # solving it switches to the 3-day schedule
        it = item(db, T0 + 2 * DAY + 10)
        assert (it.state, it.reason, it.due_at) == ("upcoming", "review", T0 + 5 * DAY)


def test_passes_walk_through_3_7_21_then_retire(tmp_path: Path) -> None:
    with DB(tmp_path / "db") as db:
        sub(db, "AC", T0)
        t = T0 + 3 * DAY
        for stage, gap in enumerate(review.INTERVALS_DAYS):
            it = item(db, t)
            assert (it.state, it.stage) == ("due", stage)
            review.start(db, "p", 1, "cpp", t)
            sub(db, "AC", t + 600)
            done = review.on_accepted(db, "p", 1, t + 600)
            assert done is not None and done.outcome == "pass"
            nxt = review.INTERVALS_DAYS[stage + 1] if stage + 1 < 3 else None
            assert done.next_days == nxt
            t = t + 600 + (gap if nxt is None else nxt) * DAY
        assert item(db, t + 365 * DAY).state == "retired"
        assert review.queue(db, t) == []


def test_shaky_review_steps_back(tmp_path: Path) -> None:
    with DB(tmp_path / "db") as db:
        sub(db, "AC", T0)
        t1 = T0 + 3 * DAY
        review.start(db, "p", 1, "cpp", t1)
        sub(db, "AC", t1 + 60)
        assert review.on_accepted(db, "p", 1, t1 + 60) is not None  # pass -> stage 1 (7 days)
        t2 = t1 + 60 + 7 * DAY
        review.start(db, "p", 1, "cpp", t2)
        for i in range(review.SHAKY_FAILS + 1):
            sub(db, "WA", t2 + 10 * (i + 1))
        sub(db, "AC", t2 + 100)
        done = review.on_accepted(db, "p", 1, t2 + 100)
        assert done is not None and done.outcome == "shaky" and done.failed_submits == 3
        it = item(db, t2 + 101)
        assert it.stage == 0 and it.due_at == t2 + 100 + 3 * DAY


def test_two_failed_submits_still_pass(tmp_path: Path) -> None:
    with DB(tmp_path / "db") as db:
        sub(db, "AC", T0)
        t = T0 + 3 * DAY
        review.start(db, "p", 1, "cpp", t)
        sub(db, "WA", t + 1)
        sub(db, "RE", t + 2)
        sub(db, "AC", t + 3)
        done = review.on_accepted(db, "p", 1, t + 3)
        assert done is not None and done.outcome == "pass" and done.failed_submits == 2


def test_giving_up_or_lapsing_resets_to_one_day(tmp_path: Path) -> None:
    with DB(tmp_path / "db") as db:
        sub(db, "AC", T0)
        t = T0 + 3 * DAY
        review.start(db, "p", 1, "cpp", t)
        assert item(db, t + 1).state == "in_progress"
        done = review.give_up(db, "p", 1, t + 3600)
        assert done is not None and done.outcome == "fail" and done.next_days == 1
        it = item(db, t + 3600 + 1)
        assert (it.state, it.reason, it.stage) == ("upcoming", "retry", 0)
        assert it.due_at == t + 3600 + DAY
        assert review.give_up(db, "p", 1, t + 3601) is None  # nothing open any more

    with DB(tmp_path / "db2") as db:
        sub(db, "AC", T0)
        t = T0 + 3 * DAY
        review.start(db, "p", 1, "cpp", t)
        late = t + (review.LAPSE_DAYS + 1) * DAY
        it = item(db, late)  # left open too long: counts as a fail at the lapse time
        assert (it.state, it.reason) == ("due", "retry")
        assert review.on_accepted(db, "p", 1, late) is None  # nothing open to finish


def test_start_is_idempotent_and_queue_orders_by_urgency(tmp_path: Path) -> None:
    with DB(tmp_path / "db") as db:
        sub(db, "AC", T0, slug="a")
        sub(db, "AC", T0 + DAY, slug="b")
        sub(db, "AC", T0 + 5 * DAY, slug="c")
        now = T0 + 5 * DAY
        first = review.start(db, "b", 1, "cpp", now)
        assert review.start(db, "b", 1, "cpp", now + 5) == first
        q = review.queue(db, now)
        assert [(i.slug, i.state) for i in q] == [
            ("b", "in_progress"),
            ("a", "due"),
            ("c", "upcoming"),
        ]
        assert [i.slug for i in review.queue(db, now, known={("a", 1)})] == ["a"]


runner = CliRunner()


def dsa(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.output


def test_review_cli_flow(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out = dsa("review")
    assert code == 0 and "no reviews yet" in out

    dsa("open", "max-subarray", "--no-editor", "--lang", "python")
    wd = home / "work" / "max-subarray" / "part1"
    shutil.copyfile(SOLUTIONS / "part1" / "correct.py", wd / "sol.py")
    monkeypatch.chdir(wd)
    code, out = dsa("submit", "--lang", "python")
    assert code == 0 and "part 2 unlocked" in out

    code, out = dsa("review")
    assert "nothing due" in out

    # Pretend the accepted submit happened four days ago.
    conn = sqlite3.connect(home / "db.sqlite")
    conn.execute("UPDATE attempts SET created_at = ?", (time.time() - 4 * DAY,))
    conn.commit()
    conn.close()
    code, out = dsa("review")
    assert "max-subarray" in out and "review 1/3" in out and "overdue" in out

    code, out = dsa("review", "start", "--no-editor", "--lang", "python")
    assert code == 0, out
    assert "pass" not in out
    stub = (wd / "sol.py").read_text()
    assert "class Solution" in stub and "kadane" not in stub.lower()
    (history,) = (wd / "history").glob("sol.*.py")
    assert history.read_text() == (SOLUTIONS / "part1" / "correct.py").read_text()
    code, out = dsa("review")
    assert "in progress" in out

    code, out = dsa("submit", "--lang", "python")  # the stub is empty: fails
    assert code == 1
    shutil.copyfile(SOLUTIONS / "part1" / "correct.py", wd / "sol.py")
    code, out = dsa("submit", "--lang", "python")
    assert code == 0 and "review pass" in out and "next review in 7 day(s)" in out
    code, out = dsa("review")
    assert "nothing due" in out
    code, out = dsa("review", "--all")
    assert "max-subarray" in out and "in 7 d" in out

    code, out = dsa("review", "start", "--no-editor")
    assert code == 2 and "nothing to review" in out
    code, out = dsa("review", "start", "--early", "--no-editor", "--lang", "python")
    assert code == 0
    code, out = dsa("review", "fail")
    assert code == 0 and "due again in 1 day" in out
    code, out = dsa("review", "fail")
    assert code == 2 and "no review in progress" in out
