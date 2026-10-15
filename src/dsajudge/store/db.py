"""SQLite store: practice sessions (timers), attempts, small key/value meta."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

MIGRATIONS = [
    # v1
    """
    CREATE TABLE sessions (
        id INTEGER PRIMARY KEY,
        slug TEXT NOT NULL,
        part INTEGER NOT NULL,
        lang TEXT NOT NULL,
        started_at REAL NOT NULL
    );
    CREATE INDEX sessions_part ON sessions (slug, part);
    CREATE TABLE attempts (
        id INTEGER PRIMARY KEY,
        slug TEXT NOT NULL,
        part INTEGER NOT NULL,
        lang TEXT NOT NULL,
        kind TEXT NOT NULL,
        verdict TEXT NOT NULL,
        passed INTEGER NOT NULL,
        total INTEGER NOT NULL,
        max_time_ms REAL NOT NULL,
        max_mem_kb INTEGER NOT NULL,
        elapsed_s REAL,
        failed_test TEXT,
        strict INTEGER NOT NULL DEFAULT 0,
        code_sha TEXT,
        created_at REAL NOT NULL
    );
    CREATE INDEX attempts_part ON attempts (slug, part);
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
]


@dataclass
class Attempt:
    slug: str
    part: int
    lang: str
    kind: str  # run | submit
    verdict: str
    passed: int
    total: int
    max_time_ms: float
    max_mem_kb: int
    elapsed_s: float | None = None
    failed_test: str | None = None
    strict: bool = False
    code_sha: str | None = None


class DB:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> DB:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _migrate(self) -> None:
        version = int(self.conn.execute("PRAGMA user_version").fetchone()[0])
        for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
            with self.conn:
                self.conn.executescript(script)
                self.conn.execute(f"PRAGMA user_version = {i}")

    # -------------------------------------------------------- sessions

    def start_session(self, slug: str, part: int, lang: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO sessions (slug, part, lang, started_at) VALUES (?, ?, ?, ?)",
                (slug, part, lang, time.time()),
            )

    def session_started(self, slug: str, part: int) -> float | None:
        row = self.conn.execute(
            "SELECT MAX(started_at) FROM sessions WHERE slug = ? AND part = ?", (slug, part)
        ).fetchone()
        return float(row[0]) if row and row[0] is not None else None

    # -------------------------------------------------------- attempts

    def log_attempt(self, a: Attempt) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO attempts (slug, part, lang, kind, verdict, passed, total,
                   max_time_ms, max_mem_kb, elapsed_s, failed_test, strict, code_sha, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    a.slug,
                    a.part,
                    a.lang,
                    a.kind,
                    a.verdict,
                    a.passed,
                    a.total,
                    a.max_time_ms,
                    a.max_mem_kb,
                    a.elapsed_s,
                    a.failed_test,
                    int(a.strict),
                    a.code_sha,
                    time.time(),
                ),
            )

    def solved_parts(self, slug: str) -> set[int]:
        rows = self.conn.execute(
            "SELECT DISTINCT part FROM attempts "
            "WHERE slug = ? AND kind = 'submit' AND verdict = 'AC'",
            (slug,),
        ).fetchall()
        return {int(r[0]) for r in rows}

    def has_accepted(self, slug: str, part: int, code_sha: str) -> bool:
        """Was this exact code accepted by a full `submit` on this part?"""
        row = self.conn.execute(
            "SELECT 1 FROM attempts WHERE slug = ? AND part = ? AND kind = 'submit' "
            "AND verdict = 'AC' AND code_sha = ? LIMIT 1",
            (slug, part, code_sha),
        ).fetchone()
        return row is not None

    def unlocked_part(self, slug: str, nparts: int) -> int:
        """Lowest part without an accepted submit (the last part once all are solved)."""
        solved = self.solved_parts(slug)
        for n in range(1, nparts + 1):
            if n not in solved:
                return n
        return nparts

    def last_failed_test(self, slug: str, part: int) -> str | None:
        row = self.conn.execute(
            """SELECT failed_test FROM attempts
               WHERE slug = ? AND part = ? AND failed_test IS NOT NULL
               ORDER BY id DESC LIMIT 1""",
            (slug, part),
        ).fetchone()
        return str(row[0]) if row else None

    def best_times(self, slug: str) -> dict[int, tuple[str, float]]:
        rows = self.conn.execute(
            """SELECT part, lang, MIN(max_time_ms) FROM attempts
               WHERE slug = ? AND kind = 'submit' AND verdict = 'AC' GROUP BY part, lang""",
            (slug,),
        ).fetchall()
        best: dict[int, tuple[str, float]] = {}
        for part, lang, t in rows:
            if int(part) not in best or t < best[int(part)][1]:
                best[int(part)] = (str(lang), float(t))
        return best

    def attempt_count(self, slug: str, part: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM attempts WHERE slug = ? AND part = ?", (slug, part)
        ).fetchone()
        return int(row[0])

    # -------------------------------------------------------- meta

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
