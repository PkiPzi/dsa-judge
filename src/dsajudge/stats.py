"""`dsa stats`: practice statistics computed from the attempt log (PLAN.md §9)."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, tzinfo

from dsajudge.store.db import DB, AttemptRow, ReviewRow

DAY = 86400.0
SPARK = "▁▂▃▄▅▆▇█"


@dataclass
class LangStats:
    submits: int = 0
    accepted: int = 0
    best_ms: float | None = None


@dataclass
class TagStats:
    solved: int = 0
    parts: int = 0


@dataclass
class SolveRecord:
    slug: str
    part: int
    minutes: float | None
    failed_submits: int
    lang: str


@dataclass
class Stats:
    days: int | None
    runs: int = 0
    submits: int = 0
    accepted_submits: int = 0
    # Parts whose first accepted submit falls in the window.
    parts_solved: int = 0
    first_try: int = 0
    # Over the whole bank, not the window.
    problems_total: int = 0
    parts_total: int = 0
    parts_solved_all: int = 0
    problems_solved_all: int = 0
    verdicts: dict[str, int] = field(default_factory=dict)
    langs: dict[str, LangStats] = field(default_factory=dict)
    tags: dict[str, TagStats] = field(default_factory=dict)
    median_solve_min: float | None = None
    slowest: list[SolveRecord] = field(default_factory=list)
    hardest: list[SolveRecord] = field(default_factory=list)
    activity: list[tuple[str, int]] = field(default_factory=list)
    streak: int = 0
    longest_streak: int = 0
    active_days: int = 0
    reviews_passed: int = 0
    reviews_shaky: int = 0
    reviews_failed: int = 0

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class BankPart:
    slug: str
    part: int
    tags: list[str]


def _streaks(days_active: set[date], today: date) -> tuple[int, int]:
    """(current streak ending today or yesterday, longest streak)."""
    if not days_active:
        return 0, 0
    cur = 0
    d = today if today in days_active else today - timedelta(days=1)
    while d in days_active:
        cur += 1
        d -= timedelta(days=1)
    longest = run = 0
    prev: date | None = None
    for d in sorted(days_active):
        run = run + 1 if prev is not None and d - prev == timedelta(days=1) else 1
        longest = max(longest, run)
        prev = d
    return cur, longest


def sparkline(counts: list[int]) -> str:
    top = max(counts, default=0)
    if top == 0:
        return SPARK[0] * len(counts)
    last = len(SPARK) - 1
    # A day with any practice never shows as the empty bar.
    return "".join(
        SPARK[max(1, min(round(c / top * last), last))] if c else SPARK[0] for c in counts
    )


def compute(
    rows: list[AttemptRow],
    reviews: list[ReviewRow],
    bank: list[BankPart],
    *,
    now: float,
    days: int | None = None,
    tag: str | None = None,
    tz: tzinfo | None = None,
    activity_days: int = 14,
    top: int = 5,
) -> Stats:
    """Statistics over ``rows`` (all attempts, oldest first); ``days`` limits the window."""
    since = now - days * DAY if days else 0.0
    bank_keys = {(b.slug, b.part) for b in bank if tag is None or tag in b.tags}
    rows = [r for r in rows if (r.slug, r.part) in bank_keys] if tag else rows
    st = Stats(days=days)
    st.parts_total = len(bank_keys)
    st.problems_total = len({s for s, _ in bank_keys})

    # First accepted submit per part, over all time.
    first_ac: dict[tuple[str, int], AttemptRow] = {}
    failed_before: Counter[tuple[str, int]] = Counter()
    for r in rows:
        if r.kind != "submit":
            continue
        key = (r.slug, r.part)
        if key in first_ac:
            continue
        if r.verdict == "AC":
            first_ac[key] = r
        else:
            failed_before[key] += 1
    solved_all = {k for k in first_ac if k in bank_keys}
    st.parts_solved_all = len(solved_all)
    per_problem: dict[str, int] = Counter(s for s, _ in bank_keys)
    solved_per_problem: Counter[str] = Counter(s for s, _ in solved_all)
    st.problems_solved_all = sum(1 for s, n in per_problem.items() if solved_per_problem[s] >= n)

    # Windowed counters.
    win = [r for r in rows if r.created_at >= since]
    verdicts: Counter[str] = Counter()
    for r in win:
        if r.kind == "run":
            st.runs += 1
            continue
        st.submits += 1
        verdicts[r.verdict] += 1
        ls = st.langs.setdefault(r.lang, LangStats())
        ls.submits += 1
        if r.verdict == "AC":
            st.accepted_submits += 1
            ls.accepted += 1
            if ls.best_ms is None or r.max_time_ms < ls.best_ms:
                ls.best_ms = r.max_time_ms
    st.verdicts = dict(verdicts.most_common())

    # Solves in the window.
    records: list[SolveRecord] = []
    for key, r in first_ac.items():
        if key not in bank_keys or r.created_at < since:
            continue
        failed = failed_before[key]
        records.append(
            SolveRecord(
                r.slug,
                r.part,
                r.elapsed_s / 60 if r.elapsed_s is not None else None,
                failed,
                r.lang,
            )
        )
    st.parts_solved = len(records)
    st.first_try = sum(1 for x in records if x.failed_submits == 0)
    minutes = [x.minutes for x in records if x.minutes is not None]
    st.median_solve_min = statistics.median(minutes) if minutes else None
    timed = [x for x in records if x.minutes is not None]
    st.slowest = sorted(timed, key=lambda x: -(x.minutes or 0.0))[:top]
    st.hardest = sorted(
        (x for x in records if x.failed_submits), key=lambda x: (-x.failed_submits, x.slug)
    )[:top]

    # Per-tag progress (whole bank).
    for b in bank:
        if tag is not None and tag not in b.tags:
            continue
        for t in b.tags or ["untagged"]:
            ts = st.tags.setdefault(t, TagStats())
            ts.parts += 1
            if (b.slug, b.part) in solved_all:
                ts.solved += 1

    # Activity and streaks use every attempt (runs count as practice), not just the window.
    def day_of(ts: float) -> date:
        return datetime.fromtimestamp(ts, tz).date()

    per_day: Counter[date] = Counter(day_of(r.created_at) for r in rows)
    today = day_of(now)
    st.activity = [
        ((today - timedelta(days=i)).isoformat(), per_day[today - timedelta(days=i)])
        for i in range(activity_days - 1, -1, -1)
    ]
    st.streak, st.longest_streak = _streaks(set(per_day), today)
    st.active_days = len({day_of(r.created_at) for r in win})

    # Reviews (finished ones, in the window).
    by_review: dict[str, int] = defaultdict(int)
    for rv in reviews:
        if rv.finished_at is None or rv.finished_at < since:
            continue
        if tag is not None and (rv.slug, rv.part) not in bank_keys:
            continue
        by_review[rv.outcome or ""] += 1
    st.reviews_passed = by_review["pass"]
    st.reviews_shaky = by_review["shaky"]
    st.reviews_failed = by_review["fail"]
    return st


def collect(
    db: DB,
    bank: list[BankPart],
    *,
    now: float,
    days: int | None = None,
    tag: str | None = None,
    tz: tzinfo | None = None,
) -> Stats:
    return compute(db.attempt_rows(), db.all_reviews(), bank, now=now, days=days, tag=tag, tz=tz)
