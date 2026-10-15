"""Spaced repetition (PLAN.md §9): re-solve accepted parts after 3, 7 and 21 days.

The schedule is derived from the attempt log and the ``reviews`` table, never stored:

* A part is first due ``INTERVALS_DAYS[0]`` days after its first accepted submit.
* ``dsa review start`` opens a fresh stub and starts a *review*. An accepted submit finishes it:
  ``pass`` moves to the next interval, ``shaky`` (more than ``SHAKY_FAILS`` failed submits on the
  way) steps back one interval, and giving up (``dsa review fail``) or leaving it open for
  ``LAPSE_DAYS`` is a ``fail``: back to the start, due again after ``RETRY_DAYS``.
* A part that was submitted but never accepted is due ``RETRY_DAYS`` after its last failed submit,
  so failed ones come back sooner than solved ones.
* After the last interval passes, the part is retired from the queue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from dsajudge.store.db import DB, ReviewRow

INTERVALS_DAYS: tuple[int, ...] = (3, 7, 21)
RETRY_DAYS = 1
SHAKY_FAILS = 2
LAPSE_DAYS = 7
DAY = 86400.0

State = Literal["due", "upcoming", "in_progress", "retired"]
Reason = Literal["unfinished", "review", "retry"]


@dataclass
class ReviewItem:
    slug: str
    part: int
    state: State
    reason: Reason
    # Reviews passed so far (0 = never reviewed); the next interval is INTERVALS_DAYS[stage].
    stage: int
    due_at: float | None
    started_at: float | None = None
    review_id: int | None = None

    def days_until_due(self, now: float) -> float:
        assert self.due_at is not None
        return (self.due_at - now) / DAY


def _effective(reviews: list[ReviewRow], now: float) -> list[tuple[str, float]]:
    """(outcome, time) per review in order; an overdue open review counts as a lapse."""
    out: list[tuple[str, float]] = []
    for r in reviews:
        if r.finished_at is not None and r.outcome:
            out.append((r.outcome, r.finished_at))
        elif now - r.started_at > LAPSE_DAYS * DAY:
            out.append(("fail", r.started_at + LAPSE_DAYS * DAY))
    return out


def item_for(db: DB, slug: str, part: int, now: float) -> ReviewItem | None:
    """Where one part stands in the queue, or None if it was never submitted."""
    reviews = db.reviews_for(slug, part)
    first_ac = db.first_ac_at(slug, part)
    if first_ac is None:
        failed = db.last_failed_submit_at(slug, part)
        if failed is None:
            return None
        due = failed + RETRY_DAYS * DAY
        return ReviewItem(slug, part, "due" if due <= now else "upcoming", "unfinished", 0, due)

    stage, anchor, after_fail = 0, first_ac, False
    for outcome, at in _effective(reviews, now):
        if outcome == "pass":
            stage, after_fail = stage + 1, False
        elif outcome == "shaky":
            stage, after_fail = max(stage - 1, 0), False
        else:
            stage, after_fail = 0, True
        anchor = at

    open_row = next((r for r in reversed(reviews) if r.finished_at is None), None)
    if open_row is not None and now - open_row.started_at <= LAPSE_DAYS * DAY:
        return ReviewItem(
            slug,
            part,
            "in_progress",
            "review",
            stage,
            None,
            started_at=open_row.started_at,
            review_id=open_row.id,
        )
    if stage >= len(INTERVALS_DAYS):
        return ReviewItem(slug, part, "retired", "review", stage, None)
    days = RETRY_DAYS if after_fail else INTERVALS_DAYS[stage]
    due = anchor + days * DAY
    return ReviewItem(
        slug,
        part,
        "due" if due <= now else "upcoming",
        "retry" if after_fail else "review",
        stage,
        due,
    )


def queue(db: DB, now: float, *, known: set[tuple[str, int]] | None = None) -> list[ReviewItem]:
    """Every part with a pending, in-progress or upcoming review, most overdue first.

    ``known`` restricts the queue to parts that still exist in the bank.
    """
    items: list[ReviewItem] = []
    for slug, part in db.submitted_parts():
        if known is not None and (slug, part) not in known:
            continue
        it = item_for(db, slug, part, now)
        if it is not None and it.state != "retired":
            items.append(it)
    order = {"in_progress": 0, "due": 1, "upcoming": 2}
    items.sort(key=lambda i: (order[i.state], i.due_at or 0.0, i.slug, i.part))
    return items


def start(db: DB, slug: str, part: int, lang: str, now: float) -> int:
    """Open a review; there can be only one open per part."""
    it = item_for(db, slug, part, now)
    if it is not None and it.review_id is not None:
        return it.review_id
    return db.start_review(slug, part, lang, at=now)


@dataclass
class Finished:
    outcome: Literal["pass", "shaky", "fail"]
    failed_submits: int
    # Days until the part is due again; None when it is retired.
    next_days: int | None


def _next_days(db: DB, slug: str, part: int, now: float) -> int | None:
    it = item_for(db, slug, part, now)
    if it is None or it.due_at is None:
        return None
    return max(round((it.due_at - now) / DAY), 0)


def on_accepted(db: DB, slug: str, part: int, now: float) -> Finished | None:
    """Called after an accepted submit: finishes the part's open review, if any."""
    it = item_for(db, slug, part, now)
    if it is None or it.review_id is None or it.started_at is None:
        return None
    failed = db.failed_submits_since(slug, part, it.started_at, now)
    outcome: Literal["pass", "shaky"] = "shaky" if failed > SHAKY_FAILS else "pass"
    db.finish_review(it.review_id, outcome, failed, at=now)
    return Finished(outcome, failed, _next_days(db, slug, part, now))


def give_up(db: DB, slug: str, part: int, now: float) -> Finished | None:
    """Give up on an open review: it counts as a fail and comes back after RETRY_DAYS."""
    it = item_for(db, slug, part, now)
    if it is None or it.review_id is None or it.started_at is None:
        return None
    failed = db.failed_submits_since(slug, part, it.started_at, now)
    db.finish_review(it.review_id, "fail", failed, at=now)
    return Finished("fail", failed, _next_days(db, slug, part, now))
