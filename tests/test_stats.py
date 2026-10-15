"""`dsa stats` numbers, computed from synthetic attempt logs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from dsajudge import stats
from dsajudge.cli import app
from dsajudge.store.db import DB, Attempt, AttemptRow, ReviewRow

DAY = 86400.0
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC).timestamp()


def row(
    slug: str,
    part: int,
    verdict: str,
    days_ago: float,
    *,
    kind: str = "submit",
    lang: str = "cpp",
    elapsed_min: float | None = 10,
    ms: float = 5.0,
) -> AttemptRow:
    return AttemptRow(
        slug,
        part,
        lang,
        kind,
        verdict,
        1,
        1,
        ms,
        elapsed_min * 60 if elapsed_min is not None else None,
        NOW - days_ago * DAY,
    )


BANK = [
    stats.BankPart("a", 1, ["dp"]),
    stats.BankPart("a", 2, ["dp"]),
    stats.BankPart("b", 1, ["graphs"]),
    stats.BankPart("c", 1, []),
]


def test_counts_and_rates() -> None:
    rows = [
        row("a", 1, "WA", 5.0),
        row("a", 1, "AC", 5.0, elapsed_min=30),
        row("a", 2, "AC", 3.0, lang="python", elapsed_min=10, ms=40),
        row("a", 1, "AC", 1.0, kind="run"),
        row("b", 1, "TLE", 2.0),
    ]
    st = stats.compute(rows, [], BANK, now=NOW, tz=UTC)
    assert (st.submits, st.runs, st.accepted_submits) == (4, 1, 2)
    assert (st.parts_solved, st.first_try) == (2, 1)
    assert st.parts_solved_all == 2 and st.parts_total == 4
    assert st.problems_solved_all == 1 and st.problems_total == 3  # a is fully solved
    assert st.median_solve_min == 20
    assert st.verdicts == {"AC": 2, "WA": 1, "TLE": 1}
    assert st.langs["cpp"].submits == 3 and st.langs["python"].best_ms == 40
    assert [(r.slug, r.part, r.failed_submits) for r in st.hardest] == [("a", 1, 1)]
    assert st.slowest[0].minutes == 30
    assert st.tags["dp"].solved == 2 and st.tags["dp"].parts == 2
    assert st.tags["graphs"].solved == 0 and "untagged" in st.tags


def test_window_and_tag_filters() -> None:
    rows = [
        row("a", 1, "AC", 40.0),
        row("b", 1, "AC", 2.0),
        row("b", 1, "WA", 1.0),
    ]
    st = stats.compute(rows, [], BANK, now=NOW, days=7, tz=UTC)
    assert st.submits == 2 and st.parts_solved == 1  # only b's first AC is in the window
    assert st.parts_solved_all == 2  # whole-bank figures ignore the window
    st = stats.compute(rows, [], BANK, now=NOW, tag="dp", tz=UTC)
    assert st.submits == 1 and st.parts_total == 2 and set(st.tags) == {"dp"}


def test_streaks_and_activity() -> None:
    rows = [row("a", 1, "WA", d, kind="run") for d in (0.2, 1.2, 2.2, 5.2, 6.2, 7.2, 8.2)]
    st = stats.compute(rows, [], BANK, now=NOW, tz=UTC, activity_days=10)
    assert st.streak == 3 and st.longest_streak == 4 and st.active_days == 7
    assert len(st.activity) == 10 and st.activity[-1] == ("2026-10-15", 1)
    assert stats.sparkline([0, 1, 2, 4]) == "▁▃▅█"
    assert stats.sparkline([0, 1, 100]) == "▁▂█"
    assert stats.sparkline([0, 0]) == "▁▁"

    # A streak that ended yesterday is still alive today.
    st = stats.compute([row("a", 1, "WA", 1.2, kind="run")], [], BANK, now=NOW, tz=UTC)
    assert st.streak == 1
    st = stats.compute([row("a", 1, "WA", 3.2, kind="run")], [], BANK, now=NOW, tz=UTC)
    assert st.streak == 0 and st.longest_streak == 1


def test_reviews_counted() -> None:
    reviews = [
        ReviewRow(1, "a", 1, "cpp", NOW - 3 * DAY, NOW - 3 * DAY + 60, "pass", 0),
        ReviewRow(2, "a", 1, "cpp", NOW - 2 * DAY, NOW - 2 * DAY + 60, "shaky", 4),
        ReviewRow(3, "b", 1, "cpp", NOW - DAY, NOW - DAY + 60, "fail", 1),
        ReviewRow(4, "b", 1, "cpp", NOW - 100, None, None, 0),
    ]
    st = stats.compute([], reviews, BANK, now=NOW, tz=UTC)
    assert (st.reviews_passed, st.reviews_shaky, st.reviews_failed) == (1, 1, 1)


def test_empty_log() -> None:
    st = stats.compute([], [], [], now=NOW, tz=UTC)
    assert st.submits == 0 and st.median_solve_min is None and st.streak == 0


def test_stats_cli(home: Path) -> None:
    from dsajudge.model.canon import dumps  # noqa: F401  (import check only)

    runner = CliRunner()
    runner.invoke(
        app, ["import", str(Path(__file__).parents[1] / "fixtures/problems/max-subarray")]
    )
    with DB(home / "db.sqlite") as db:
        db.log_attempt(Attempt("max-subarray", 1, "cpp", "submit", "WA", 0, 3, 1.0, 1))
        db.log_attempt(
            Attempt("max-subarray", 1, "cpp", "submit", "AC", 3, 3, 4.0, 1, elapsed_s=600)
        )
    res = runner.invoke(app, ["stats"])
    assert res.exit_code == 0, res.output
    assert "1/3 parts solved" in res.output and "50% accepted" in res.output
    assert "median time to first AC: 10 min" in res.output
    res = runner.invoke(app, ["stats", "--json", "--days", "7"])
    data = json.loads(res.output)
    assert data["submits"] == 2 and data["parts_solved"] == 1 and data["days"] == 7
    res = runner.invoke(app, ["stats", "--tag", "nothing"])
    assert res.exit_code == 0 and "0 submits" in res.output
