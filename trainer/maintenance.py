"""Data maintenance: audio retention cleanup, «Удалить все данные», export."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from trainer import keystore

log = logging.getLogger(__name__)
CLEANUP_INTERVAL_S = 6 * 3600


def _safe_unlink(core: Any, rel: str) -> None:
    path = (core.data_dir / rel).resolve()
    if core.audio_dir.resolve() in path.parents and path.exists():
        path.unlink()


def cleanup_audio(core: Any, now: datetime | None = None) -> int:
    """Delete stored turn audio older than the retention period (all of it when it is 0)."""
    days = core.settings.get().audio_retention_days
    now = now or datetime.now(UTC)
    cutoff = (now + timedelta(seconds=1)) if days <= 0 else now - timedelta(days=days)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    removed = 0
    for row in core.sessions.expired_audio(cutoff_iso):
        _safe_unlink(core, row["audio_path"])
        core.sessions.clear_audio_path(row["id"])
        removed += 1
    for row in core.db.all(
        "SELECT id, audio_path FROM pron_attempt WHERE audio_path IS NOT NULL AND created_at < ?",
        (cutoff_iso,),
    ):
        _safe_unlink(core, row["audio_path"])
        core.db.execute("UPDATE pron_attempt SET audio_path = NULL WHERE id = ?", (row["id"],))
        removed += 1
    _remove_empty_dirs(core.audio_dir)
    if removed:
        log.info("audio cleanup removed %d files", removed)
    return removed


def _remove_empty_dirs(root: Path) -> None:
    if not root.exists():
        return
    for d in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        with contextlib.suppress(OSError):
            d.rmdir()


async def cleanup_loop(core: Any) -> None:
    while True:
        try:
            await asyncio.to_thread(cleanup_audio, core)
        except Exception:
            log.exception("audio cleanup failed")
        await asyncio.sleep(CLEANUP_INTERVAL_S)


def delete_all_data(core: Any, include_keys: bool) -> dict[str, Any]:
    """Clear sessions, history, statistics, vocabulary and all audio. Settings stay.
    Connections and their keys are removed only with include_keys."""
    db = core.db
    with db.transaction() as conn:
        for table in (
            "correction",
            "turn",
            "session",
            "pattern",
            "vocab",
            "pron_attempt",
            "pron_sound",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM scenario WHERE builtin = 0")
    if core.audio_dir.exists():
        shutil.rmtree(core.audio_dir, ignore_errors=True)
    removed_keys = 0
    if include_keys:
        ids = [int(r["id"]) for r in db.all("SELECT id FROM connection")]
        keystore.delete_all(ids)
        db.execute("DELETE FROM connection")
        removed_keys = len(ids)
    db.execute("VACUUM")
    return {"ok": True, "removed_keys": removed_keys}
