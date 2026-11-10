"""SQLite database with versioned migrations (docs/design.md, «Данные и хранение»)."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE connection (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        base_url TEXT NOT NULL,
        model TEXT NOT NULL,
        extra_params TEXT NOT NULL DEFAULT '{}',
        json_mode INTEGER NOT NULL DEFAULT 1,
        key_last4 TEXT NOT NULL DEFAULT '',
        is_active INTEGER NOT NULL DEFAULT 0,
        last_test_ok INTEGER,
        last_test_latency_ms INTEGER,
        last_test_message TEXT,
        last_test_at TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    CREATE TABLE setting (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE scenario (
        id TEXT PRIMARY KEY,
        builtin INTEGER NOT NULL DEFAULT 0,
        title_ru TEXT NOT NULL,
        title_en TEXT NOT NULL DEFAULT '',
        description_ru TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL,
        setting TEXT NOT NULL DEFAULT '',
        goals TEXT NOT NULL DEFAULT '[]',
        openers TEXT NOT NULL DEFAULT '[]',
        sort_order INTEGER NOT NULL DEFAULT 100,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    CREATE TABLE session (
        id TEXT PRIMARY KEY,
        scenario_id TEXT NOT NULL,
        scenario_title_ru TEXT NOT NULL DEFAULT '',
        level TEXT NOT NULL,
        correction_mode TEXT NOT NULL,
        connection_id INTEGER,
        connection_name TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        started_at TEXT NOT NULL,
        ended_at TEXT,
        history_summary TEXT NOT NULL DEFAULT '',
        summary TEXT
    );
    CREATE TABLE turn (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES session(id) ON DELETE CASCADE,
        idx INTEGER NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('user', 'tutor')),
        text TEXT NOT NULL DEFAULT '',
        words TEXT,
        audio_path TEXT,
        timing TEXT,
        interrupted INTEGER NOT NULL DEFAULT 0,
        corrections_status TEXT,
        better TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE INDEX turn_session ON turn(session_id, idx);
    CREATE TABLE correction (
        id TEXT PRIMARY KEY,
        turn_id TEXT NOT NULL REFERENCES turn(id) ON DELETE CASCADE,
        session_id TEXT NOT NULL REFERENCES session(id) ON DELETE CASCADE,
        quote TEXT NOT NULL,
        fix TEXT NOT NULL,
        type TEXT NOT NULL,
        note_ru TEXT NOT NULL,
        pattern TEXT NOT NULL,
        start INTEGER,
        "end" INTEGER,
        created_at TEXT NOT NULL
    );
    CREATE INDEX correction_session ON correction(session_id);
    CREATE TABLE pattern (
        key TEXT PRIMARY KEY,
        count INTEGER NOT NULL DEFAULT 0,
        sessions_count INTEGER NOT NULL DEFAULT 0,
        last_seen_at TEXT,
        last_session_id TEXT,
        clean_streak INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL DEFAULT 'active'
    );
    CREATE TABLE vocab (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phrase TEXT NOT NULL,
        translation_ru TEXT NOT NULL DEFAULT '',
        context TEXT NOT NULL DEFAULT '',
        session_id TEXT,
        interval_days INTEGER NOT NULL DEFAULT 0,
        reviews INTEGER NOT NULL DEFAULT 0,
        next_review_at TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE pron_attempt (
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL DEFAULT 'reading',
        phrase_id TEXT,
        text TEXT NOT NULL,
        audio_path TEXT,
        score REAL,
        details TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    );
    CREATE TABLE pron_sound (
        phone TEXT PRIMARY KEY,
        attempts INTEGER NOT NULL DEFAULT 0,
        errors INTEGER NOT NULL DEFAULT 0,
        approx INTEGER NOT NULL DEFAULT 0,
        sensitivity_offset REAL NOT NULL DEFAULT 0,
        updated_at TEXT
    );
    """,
]


class Database:
    """Thread-safe wrapper around one SQLite connection (single-user desktop app)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self.migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def migrate(self) -> int:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
            )
            row = self._conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
            current = int(row[0] or 0)
            for version, script in enumerate(MIGRATIONS, start=1):
                if version <= current:
                    continue
                self._conn.execute("BEGIN")
                try:
                    for statement in _split_sql(script):
                        self._conn.execute(statement)
                    self._conn.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise
            return len(MIGRATIONS)

    def schema_version(self) -> int:
        row = self.one("SELECT MAX(version) AS v FROM schema_version")
        return int(row["v"]) if row and row["v"] is not None else 0

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def execute(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return (
                int(cur.lastrowid or 0)
                if sql.lstrip().upper().startswith("INSERT")
                else cur.rowcount
            )

    def all(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def one(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            row: sqlite3.Row | None = self._conn.execute(sql, params).fetchone()
            return row


def _split_sql(script: str) -> list[str]:
    return [s.strip() for s in script.split(";") if s.strip()]
