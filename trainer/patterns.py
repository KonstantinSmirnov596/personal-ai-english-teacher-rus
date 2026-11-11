"""Recurring mistakes: counters per pattern key, focus and closing (plan item 2.5).

Rules (docs/design.md, «Правки и комментарии на русском»):
- a key seen in FOCUS_STREAK sessions in a row goes into the practice focus;
- a key not seen for CLOSE_AFTER sessions in a row (sessions with checked turns) is closed;
- up to FOCUS_MAX focus keys (most frequent first) go into the tutor's system prompt.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from trainer.data_files import pattern_by_key
from trainer.storage.repo import now_iso

FOCUS_STREAK = 3
CLOSE_AFTER = 3
FOCUS_MAX = 5
IGNORED = {"other"}


def _row(db: Any, key: str) -> Any:
    row = db.one("SELECT * FROM pattern WHERE key = ?", (key,))
    if row is None:
        db.execute("INSERT INTO pattern(key) VALUES (?)", (key,))
        row = db.one("SELECT * FROM pattern WHERE key = ?", (key,))
    return row


def session_checked_turns(core: Any, session_id: str) -> int:
    row = core.db.one(
        "SELECT COUNT(*) AS n FROM turn WHERE session_id = ? AND role = 'user'"
        " AND corrections_status IN ('ok', 'empty')",
        (session_id,),
    )
    return int(row["n"]) if row else 0


def update_after_session(core: Any, session_id: str) -> list[dict[str, Any]]:
    """Apply one finished session to the counters. Returns entries for the summary."""
    db = core.db
    counts: Counter[str] = Counter(
        r["pattern"]
        for r in db.all("SELECT pattern FROM correction WHERE session_id = ?", (session_id,))
        if r["pattern"] not in IGNORED
    )
    checked = session_checked_turns(core, session_id)
    titles = pattern_by_key()
    out: list[dict[str, Any]] = []
    now = now_iso()
    for key, n in counts.items():
        row = _row(db, key)
        sessions = int(row["sessions_count"]) + 1
        streak = int(row["session_streak"]) + 1
        state = row["state"]
        if streak >= FOCUS_STREAK:
            state = "focus"
        elif state not in ("focus",):
            state = "recurring" if sessions >= 2 else "new"
        db.execute(
            "UPDATE pattern SET count = count + ?, sessions_count = ?, session_streak = ?,"
            " clean_streak = 0, last_seen_at = ?, last_session_id = ?, state = ? WHERE key = ?",
            (n, sessions, streak, now, session_id, state, key),
        )
        out.append(_entry(key, titles, n, streak, state, 0))
    if checked > 0:
        for row in db.all("SELECT * FROM pattern WHERE state != 'closed'"):
            if row["key"] in counts:
                continue
            clean = int(row["clean_streak"]) + 1
            state = "closed" if clean >= CLOSE_AFTER else row["state"]
            db.execute(
                "UPDATE pattern SET clean_streak = ?, session_streak = 0, state = ? WHERE key = ?",
                (clean, state, row["key"]),
            )
            if state == "closed":
                out.append(_entry(row["key"], titles, 0, 0, "closed", clean))
    return out


def _entry(
    key: str, titles: dict[str, Any], n: int, streak: int, state: str, clean: int
) -> dict[str, Any]:
    info = titles.get(key, {})
    return {
        "key": key,
        "title_ru": info.get("title_ru", key),
        "hint_ru": info.get("hint_ru", ""),
        "count_in_session": n,
        "session_streak": streak,
        "clean_streak": clean,
        "state": state,
    }


def focus_keys(core: Any) -> list[str]:
    rows = core.db.all(
        "SELECT key FROM pattern WHERE state = 'focus' ORDER BY count DESC, key LIMIT ?",
        (FOCUS_MAX,),
    )
    return [r["key"] for r in rows]


def focus_lines(core: Any) -> list[str]:
    titles = pattern_by_key()
    lines = []
    for key in focus_keys(core):
        elicit = titles.get(key, {}).get("elicit_en")
        if elicit:
            lines.append(str(elicit))
    return lines


def all_patterns(core: Any) -> list[dict[str, Any]]:
    titles = pattern_by_key()
    out = []
    for r in core.db.all("SELECT * FROM pattern ORDER BY state = 'closed', count DESC, key"):
        info = titles.get(r["key"], {})
        out.append(
            {
                "key": r["key"],
                "title_ru": info.get("title_ru", r["key"]),
                "hint_ru": info.get("hint_ru", ""),
                "count": r["count"],
                "sessions_count": r["sessions_count"],
                "session_streak": r["session_streak"],
                "clean_streak": r["clean_streak"],
                "last_seen_at": r["last_seen_at"],
                "state": r["state"],
            }
        )
    return out
