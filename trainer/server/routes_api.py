"""HTTP routes under /api (docs/protocol.md, «HTTP»)."""

from __future__ import annotations

import csv
import io
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from trainer import i18n_ru, keystore
from trainer.core import Core, endpoint_from_row
from trainer.llm.client import Endpoint, validate_base_url, validate_extra_params
from trainer.logging_setup import register_secret
from trainer.model_registry import KOKORO_VOICES
from trainer.server.app import ApiError
from trainer.settings import SettingsPatch

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def core_of(request: Request) -> Core:
    core: Core = request.app.state.core
    return core


# --- health -----------------------------------------------------------------------------------


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    core = core_of(request)
    gpu: dict[str, Any] | None = None
    try:
        import torch

        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            gpu = {
                "name": torch.cuda.get_device_name(0),
                "vram_used_mb": int((total - free) // 2**20),
                "vram_total_mb": int(total // 2**20),
            }
    except Exception:
        gpu = None
    active = core.connections.active()
    return {
        "status": "ok" if core.models.ready else ("error" if core.models.error else "loading"),
        "gpu": gpu,
        "models": {
            "stt": core.models.stt,
            "tts": core.models.tts,
            "phoneme": core.models.phoneme,
        },
        "active_connection": core.connection_ref(active) if active else None,
        "problems": (
            [{"code": core.models.problem, "message_ru": i18n_ru.error_text(core.models.problem)}]
            if core.models.problem
            else []
        ),
    }


# --- connections ------------------------------------------------------------------------------

TEMPLATES = [
    {
        "id": "deepseek",
        "name_ru": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "models": ["deepseek-flash", "deepseek-v4-pro"],
        "extra_params": {"thinking": {"type": "disabled"}},
        "json_mode": True,
    },
    {
        "id": "openai_compatible",
        "name_ru": "Другой OpenAI-совместимый",
        "base_url": "",
        "model": "",
        "models": [],
        "extra_params": {},
        "json_mode": True,
    },
]


class ConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(min_length=1, max_length=500, repr=False)
    extra_params: dict[str, Any] = Field(default_factory=dict)
    json_mode: bool = True


class ConnectionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    api_key: str | None = Field(default=None, min_length=1, max_length=500, repr=False)
    extra_params: dict[str, Any] | None = None
    json_mode: bool | None = None


class ConnectionTest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(min_length=1, max_length=500, repr=False)
    extra_params: dict[str, Any] = Field(default_factory=dict)
    json_mode: bool = True


def _check_fields(base_url: str | None, extra: Any) -> None:
    if base_url is not None and not validate_base_url(base_url):
        raise ApiError(422, "validation", i18n_ru.INVALID_URL)
    if extra is not None and not validate_extra_params(extra):
        raise ApiError(422, "validation", i18n_ru.INVALID_EXTRA)


def _get_conn(core: Core, conn_id: int) -> Any:
    row = core.connections.get(conn_id)
    if row is None:
        raise ApiError(404, "not_found")
    return row


@router.get("/connections")
async def list_connections(request: Request) -> list[dict[str, Any]]:
    core = core_of(request)
    return [core.connections.to_api(r) for r in core.connections.list()]


@router.get("/connections/templates")
async def connection_templates() -> list[dict[str, Any]]:
    return TEMPLATES


@router.post("/connections", status_code=201)
async def create_connection(request: Request, body: ConnectionCreate) -> dict[str, Any]:
    core = core_of(request)
    _check_fields(body.base_url, body.extra_params)
    key = body.api_key.strip()
    conn_id = core.connections.create(
        name=body.name.strip(),
        base_url=body.base_url.strip(),
        model=body.model.strip(),
        extra_params=body.extra_params,
        json_mode=body.json_mode,
        key_last4=keystore.last4(key),
    )
    keystore.save_key(conn_id, key)
    return core.connections.to_api(_get_conn(core, conn_id))


@router.patch("/connections/{conn_id}")
async def patch_connection(request: Request, conn_id: int, body: ConnectionPatch) -> dict[str, Any]:
    core = core_of(request)
    _get_conn(core, conn_id)
    _check_fields(body.base_url, body.extra_params)
    fields = body.model_dump(exclude_none=True, exclude={"api_key"})
    for k in ("name", "base_url", "model"):
        if k in fields:
            fields[k] = fields[k].strip()
    if body.api_key is not None:
        key = body.api_key.strip()
        keystore.save_key(conn_id, key)
        fields["key_last4"] = keystore.last4(key)
    core.connections.update(conn_id, fields)
    return core.connections.to_api(_get_conn(core, conn_id))


@router.delete("/connections/{conn_id}", status_code=204)
async def delete_connection(request: Request, conn_id: int) -> Response:
    core = core_of(request)
    row = _get_conn(core, conn_id)
    if row["is_active"]:
        raise ApiError(409, "conflict", i18n_ru.ACTIVE_DELETE)
    keystore.delete_key(conn_id)
    core.connections.delete(conn_id)
    return Response(status_code=204)


@router.post("/connections/{conn_id}/activate")
async def activate_connection(request: Request, conn_id: int) -> dict[str, Any]:
    core = core_of(request)
    _get_conn(core, conn_id)
    core.connections.activate(conn_id)
    return core.connections.to_api(_get_conn(core, conn_id))


async def _run_test(core: Core, ep: Endpoint) -> dict[str, Any]:
    ok, latency, detail, code = await core.llm.test(ep)
    if ok:
        return {"ok": True, "latency_ms": latency, "message_ru": i18n_ru.TEST_OK}
    text = i18n_ru.error_text(code or "llm_bad_response")
    message = f"{text} {detail}".strip() if detail else text
    return {"ok": False, "latency_ms": None, "message_ru": message[:300], "code": code}


@router.post("/connections/{conn_id}/test")
async def test_connection(request: Request, conn_id: int) -> dict[str, Any]:
    core = core_of(request)
    row = _get_conn(core, conn_id)
    key = keystore.load_key(conn_id)
    if not key:
        result = {
            "ok": False,
            "latency_ms": None,
            "message_ru": i18n_ru.error_text("llm_unauthorized"),
        }
    else:
        result = await _run_test(core, endpoint_from_row(row, key))
    core.connections.record_test(conn_id, result["ok"], result["latency_ms"], result["message_ru"])
    return result


@router.post("/connections/test")
async def test_unsaved_connection(request: Request, body: ConnectionTest) -> dict[str, Any]:
    core = core_of(request)
    _check_fields(body.base_url, body.extra_params)
    register_secret(body.api_key.strip())
    ep = Endpoint(
        base_url=body.base_url.strip(),
        model=body.model.strip(),
        api_key=body.api_key.strip(),
        extra_params=body.extra_params,
        json_mode=body.json_mode,
    )
    return await _run_test(core, ep)


# --- settings ---------------------------------------------------------------------------------


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    return core_of(request).settings.get().model_dump()


@router.patch("/settings")
async def patch_settings(request: Request, body: SettingsPatch) -> dict[str, Any]:
    core = core_of(request)
    try:
        updated = core.settings.patch(body)
    except ValueError as e:
        raise ApiError(422, "validation") from e
    if body.audio_retention_days is not None:
        from trainer.maintenance import cleanup_audio

        cleanup_audio(core)
    return updated.model_dump()


@router.get("/voices")
async def voices() -> list[dict[str, Any]]:
    return [
        {"id": v, "gender": "female" if v.startswith("af_") else "male", "accent": "en-US"}
        for v in KOKORO_VOICES
    ]


# --- scenarios --------------------------------------------------------------------------------


class ScenarioIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title_ru: str = Field(min_length=1, max_length=120)
    title_en: str = Field(default="", max_length=120)
    description_ru: str = Field(default="", max_length=500)
    role: str = Field(min_length=1, max_length=1000)
    setting: str = Field(default="", max_length=1000)
    goals: list[str] = Field(default_factory=list, max_length=10)
    openers: list[str] = Field(default_factory=list, max_length=10)


class ScenarioPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title_ru: str | None = Field(default=None, min_length=1, max_length=120)
    title_en: str | None = Field(default=None, max_length=120)
    description_ru: str | None = Field(default=None, max_length=500)
    role: str | None = Field(default=None, min_length=1, max_length=1000)
    setting: str | None = Field(default=None, max_length=1000)
    goals: list[str] | None = Field(default=None, max_length=10)
    openers: list[str] | None = Field(default=None, max_length=10)


@router.get("/scenarios")
async def list_scenarios(request: Request) -> list[dict[str, Any]]:
    core = core_of(request)
    return [core.scenarios.to_api(r) for r in core.scenarios.list()]


@router.post("/scenarios", status_code=201)
async def create_scenario(request: Request, body: ScenarioIn) -> dict[str, Any]:
    core = core_of(request)
    sid = core.scenarios.create(body.model_dump())
    row = core.scenarios.get(sid)
    return core.scenarios.to_api(row)


@router.patch("/scenarios/{scenario_id}")
async def patch_scenario(request: Request, scenario_id: str, body: ScenarioPatch) -> dict[str, Any]:
    core = core_of(request)
    row = core.scenarios.get(scenario_id)
    if row is None:
        raise ApiError(404, "not_found")
    if row["builtin"]:
        raise ApiError(409, "conflict", i18n_ru.BUILTIN_SCENARIO)
    core.scenarios.update(scenario_id, body.model_dump(exclude_none=True))
    return core.scenarios.to_api(core.scenarios.get(scenario_id))


@router.delete("/scenarios/{scenario_id}", status_code=204)
async def delete_scenario(request: Request, scenario_id: str) -> Response:
    core = core_of(request)
    row = core.scenarios.get(scenario_id)
    if row is None:
        raise ApiError(404, "not_found")
    if row["builtin"]:
        raise ApiError(409, "conflict", i18n_ru.BUILTIN_SCENARIO)
    core.scenarios.delete(scenario_id)
    return Response(status_code=204)


# --- sessions and history ---------------------------------------------------------------------


def _session_api(row: Any) -> dict[str, Any]:
    keys = row.keys()
    return {
        "id": row["id"],
        "scenario_id": row["scenario_id"],
        "scenario_title_ru": row["scenario_title_ru"],
        "level": row["level"],
        "connection_name": row["connection_name"],
        "model": row["model"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "user_turns": row["user_turns"] if "user_turns" in keys else None,
        "corrections": row["corrections"] if "corrections" in keys else None,
        "has_summary": bool(row["summary"]),
    }


def _turn_api(row: Any, corrections: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "idx": row["idx"],
        "role": row["role"],
        "text": row["text"],
        "words": json.loads(row["words"]) if row["words"] else [],
        "has_audio": bool(row["audio_path"]),
        "interrupted": bool(row["interrupted"]),
        "corrections_status": row["corrections_status"],
        "better": row["better"],
        "corrections": corrections,
        "created_at": row["created_at"],
    }


def _correction_api(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "turn_id": row["turn_id"],
        "quote": row["quote"],
        "start": row["start"],
        "end": row["end"],
        "fix": row["fix"],
        "type": row["type"],
        "note_ru": row["note_ru"],
        "pattern": row["pattern"],
    }


def session_detail(core: Core, session_id: str) -> dict[str, Any]:
    row = core.sessions.get(session_id)
    if row is None:
        raise ApiError(404, "not_found")
    by_turn: dict[str, list[dict[str, Any]]] = {}
    for c in core.sessions.corrections(session_id):
        by_turn.setdefault(c["turn_id"], []).append(_correction_api(c))
    turns = [_turn_api(t, by_turn.get(t["id"], [])) for t in core.sessions.turns(session_id)]
    detail = _session_api(row)
    detail["turns"] = turns
    detail["summary"] = json.loads(row["summary"]) if row["summary"] else None
    return detail


@router.get("/sessions")
async def list_sessions(request: Request) -> list[dict[str, Any]]:
    core = core_of(request)
    return [_session_api(r) for r in core.sessions.list()]


@router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: str) -> dict[str, Any]:
    return session_detail(core_of(request), session_id)


@router.get("/sessions/{session_id}/export")
async def export_session(request: Request, session_id: str, format: str = "json") -> Response:
    core = core_of(request)
    detail = session_detail(core, session_id)
    if format == "json":
        body = json.dumps(detail, ensure_ascii=False, indent=2)
        return Response(
            body,
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="session-{session_id}.json"'},
        )
    if format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["idx", "role", "text", "quote", "fix", "type", "note_ru", "pattern"])
        for t in detail["turns"]:
            if not t["corrections"]:
                writer.writerow([t["idx"], t["role"], t["text"], "", "", "", "", ""])
            for c in t["corrections"]:
                writer.writerow(
                    [
                        t["idx"],
                        t["role"],
                        t["text"],
                        c["quote"],
                        c["fix"],
                        c["type"],
                        c["note_ru"],
                        c["pattern"],
                    ]
                )
        return Response(
            "﻿" + buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="session-{session_id}.csv"'},
        )
    raise ApiError(422, "validation")


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(request: Request, session_id: str) -> Response:
    core = core_of(request)
    if core.sessions.get(session_id) is None:
        raise ApiError(404, "not_found")
    for p in core.sessions.delete(session_id):
        _unlink_audio(core, p)
    return Response(status_code=204)


def _unlink_audio(core: Core, rel: str) -> None:
    path = (core.data_dir / rel).resolve()
    if core.audio_dir.resolve() in path.parents and path.exists():
        path.unlink()


@router.get("/turns/{turn_id}/audio")
async def turn_audio(request: Request, turn_id: str) -> Response:
    core = core_of(request)
    row = core.sessions.get_turn(turn_id)
    if row is None or not row["audio_path"]:
        raise ApiError(404, "not_found")
    path: Path = (core.data_dir / row["audio_path"]).resolve()
    if core.audio_dir.resolve() not in path.parents or not path.exists():
        raise ApiError(404, "not_found")
    return FileResponse(path, media_type="audio/wav")


def json_error(status: int, code: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message_ru": i18n_ru.error_text(code)}}, status_code=status
    )
