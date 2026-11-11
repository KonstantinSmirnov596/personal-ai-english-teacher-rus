"""HTTP routes for patterns, vocabulary, voice preview, data export and deletion."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from trainer.llm.json_call import has_cyrillic
from trainer.maintenance import cleanup_audio, delete_all_data
from trainer.model_registry import KOKORO_VOICES
from trainer.patterns import all_patterns
from trainer.server.app import ApiError
from trainer.server.routes_api import core_of, session_detail
from trainer.speech.audio import wav_bytes
from trainer.storage.repo import now_iso

router = APIRouter(prefix="/api")

# Simple spaced repetition: days until the next review after the n-th successful review.
INTERVALS = (1, 3, 7, 14, 30, 60)


@router.get("/patterns")
async def patterns(request: Request) -> list[dict[str, Any]]:
    return all_patterns(core_of(request))


# --- vocabulary -------------------------------------------------------------------------------


class VocabIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phrase: str = Field(min_length=1, max_length=200)
    translation_ru: str = Field(default="", max_length=300)
    context: str = Field(default="", max_length=600)
    session_id: str | None = None


class VocabPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phrase: str | None = Field(default=None, min_length=1, max_length=200)
    translation_ru: str | None = Field(default=None, max_length=300)
    result: Literal["good", "again"] | None = None


def _vocab_api(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "phrase": row["phrase"],
        "translation_ru": row["translation_ru"],
        "context": row["context"],
        "interval_days": row["interval_days"],
        "reviews": row["reviews"],
        "next_review_at": row["next_review_at"],
        "created_at": row["created_at"],
    }


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def next_review(reviews: int, now: datetime) -> tuple[int, str]:
    days = INTERVALS[min(reviews, len(INTERVALS) - 1)]
    return days, _iso(now + timedelta(days=days))


@router.get("/vocab")
async def vocab_list(request: Request) -> list[dict[str, Any]]:
    rows = core_of(request).db.all("SELECT * FROM vocab ORDER BY created_at DESC")
    return [_vocab_api(r) for r in rows]


@router.get("/vocab/due")
async def vocab_due(request: Request) -> list[dict[str, Any]]:
    rows = core_of(request).db.all(
        "SELECT * FROM vocab WHERE next_review_at <= ? ORDER BY next_review_at", (now_iso(),)
    )
    return [_vocab_api(r) for r in rows]


@router.post("/vocab", status_code=201)
async def vocab_add(request: Request, body: VocabIn) -> dict[str, Any]:
    core = core_of(request)
    if has_cyrillic(body.phrase):
        raise ApiError(422, "validation")
    existing = core.db.one(
        "SELECT * FROM vocab WHERE lower(phrase) = lower(?)", (body.phrase.strip(),)
    )
    if existing is not None:
        return _vocab_api(existing)
    days, due = next_review(0, datetime.now(UTC))
    vid = core.db.execute(
        "INSERT INTO vocab(phrase, translation_ru, context, session_id, interval_days, reviews,"
        " next_review_at, created_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
        (
            body.phrase.strip(),
            body.translation_ru,
            body.context,
            body.session_id,
            days,
            due,
            now_iso(),
        ),
    )
    return _vocab_api(core.db.one("SELECT * FROM vocab WHERE id = ?", (vid,)))


@router.patch("/vocab/{vocab_id}")
async def vocab_patch(request: Request, vocab_id: int, body: VocabPatch) -> dict[str, Any]:
    core = core_of(request)
    row = core.db.one("SELECT * FROM vocab WHERE id = ?", (vocab_id,))
    if row is None:
        raise ApiError(404, "not_found")
    if body.phrase is not None:
        core.db.execute("UPDATE vocab SET phrase = ? WHERE id = ?", (body.phrase, vocab_id))
    if body.translation_ru is not None:
        core.db.execute(
            "UPDATE vocab SET translation_ru = ? WHERE id = ?", (body.translation_ru, vocab_id)
        )
    if body.result is not None:
        reviews = int(row["reviews"]) + 1 if body.result == "good" else 0
        days, due = next_review(reviews, datetime.now(UTC))
        core.db.execute(
            "UPDATE vocab SET reviews = ?, interval_days = ?, next_review_at = ? WHERE id = ?",
            (reviews, days, due, vocab_id),
        )
    return _vocab_api(core.db.one("SELECT * FROM vocab WHERE id = ?", (vocab_id,)))


@router.delete("/vocab/{vocab_id}", status_code=204)
async def vocab_delete(request: Request, vocab_id: int) -> Response:
    core = core_of(request)
    if core.db.one("SELECT id FROM vocab WHERE id = ?", (vocab_id,)) is None:
        raise ApiError(404, "not_found")
    core.db.execute("DELETE FROM vocab WHERE id = ?", (vocab_id,))
    return Response(status_code=204)


# --- voices -----------------------------------------------------------------------------------

PREVIEW_TEXT = "Hi! This is how I sound. Let's practice your English together."


class VoicePreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    voice: str
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


@router.post("/voices/preview")
async def voice_preview(request: Request, body: VoicePreview) -> Response:
    core = core_of(request)
    if body.voice not in KOKORO_VOICES:
        raise ApiError(422, "validation")
    result = await core.gpu.run(
        core.tts.synthesize, PREVIEW_TEXT, voice=body.voice, speed=body.speed
    )
    return Response(wav_bytes(result.audio, result.sample_rate), media_type="audio/wav")


# --- data -------------------------------------------------------------------------------------


class DeleteData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: Literal["DELETE"]
    include_keys: bool = False


@router.delete("/data")
async def delete_data(request: Request, body: DeleteData) -> dict[str, Any]:
    return delete_all_data(core_of(request), body.include_keys)


@router.post("/data/cleanup")
async def run_cleanup(request: Request) -> dict[str, Any]:
    return {"removed": cleanup_audio(core_of(request))}


@router.get("/export")
async def export_all(request: Request, format: str = "json") -> Response:
    core = core_of(request)
    sessions = [session_detail(core, r["id"]) for r in core.sessions.list()]
    vocab = [_vocab_api(r) for r in core.db.all("SELECT * FROM vocab ORDER BY created_at")]
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    if format == "json":
        body = json.dumps(
            {"sessions": sessions, "vocab": vocab, "patterns": all_patterns(core)},
            ensure_ascii=False,
            indent=2,
        )
        return Response(
            body,
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="trainer-{stamp}.json"'},
        )
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(
            [
                "session",
                "started_at",
                "scenario",
                "idx",
                "role",
                "text",
                "quote",
                "fix",
                "note_ru",
                "pattern",
            ]
        )
        for s in sessions:
            for t in s["turns"]:
                rows = t["corrections"] or [None]
                for c in rows:
                    w.writerow(
                        [
                            s["id"],
                            s["started_at"],
                            s["scenario_title_ru"],
                            t["idx"],
                            t["role"],
                            t["text"],
                            c["quote"] if c else "",
                            c["fix"] if c else "",
                            c["note_ru"] if c else "",
                            c["pattern"] if c else "",
                        ]
                    )
        w.writerow([])
        w.writerow(["vocab_phrase", "translation_ru", "context", "next_review_at"])
        for v in vocab:
            w.writerow([v["phrase"], v["translation_ru"], v["context"], v["next_review_at"]])
        return Response(
            "﻿" + buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="trainer-{stamp}.csv"'},
        )
    raise ApiError(422, "validation")
