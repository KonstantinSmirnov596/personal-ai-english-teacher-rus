"""HTTP routes of the reading mode (docs/protocol.md, «Произношение»)."""

from __future__ import annotations

import io
import logging
from typing import Any, Literal

import soundfile as sf
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from trainer.llm.json_call import has_cyrillic
from trainer.pron.service import PronError
from trainer.server.app import ApiError
from trainer.server.routes_api import core_of
from trainer.speech.audio import STT_RATE, FloatAudio, resample, to_mono, wav_bytes

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/pron")

MAX_AUDIO_BYTES = 8 * 1024 * 1024
MIN_SECONDS = 0.3
MAX_SECONDS = 60.0


def decode_wav(data: bytes) -> FloatAudio:
    if not data or len(data) > MAX_AUDIO_BYTES:
        raise ApiError(422, "pron_bad_audio")
    try:
        audio, rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=False)
    except Exception as e:
        raise ApiError(422, "pron_bad_audio") from e
    mono = to_mono(audio)
    seconds = len(mono) / float(rate)
    if seconds < MIN_SECONDS:
        raise ApiError(422, "audio_too_short")
    if seconds > MAX_SECONDS:
        raise ApiError(422, "audio_too_long")
    return resample(mono, int(rate), STT_RATE)


async def _assess(request: Request, text: str, audio: FloatAudio, phrase_id: str | None) -> Any:
    core = core_of(request)
    if has_cyrillic(text):
        raise ApiError(422, "pron_no_text")
    try:
        return await core.pron.assess(text, audio, source="reading", phrase_id=phrase_id)
    except PronError as e:
        raise ApiError(404 if e.code == "not_found" else 422, e.code) from e
    except Exception as e:
        log.exception("pronunciation assessment failed")
        raise ApiError(503, "pron_unavailable") from e


@router.get("/phrases")
async def phrases(request: Request) -> list[dict[str, Any]]:
    return core_of(request).pron.phrases()


@router.post("/assess")
async def assess(request: Request, phrase_id: str) -> Any:
    core = core_of(request)
    text = core.pron.phrase_text(phrase_id)
    if text is None:
        raise ApiError(404, "not_found")
    audio = decode_wav(await request.body())
    return await _assess(request, text, audio, phrase_id)


@router.post("/assess-text")
async def assess_text(
    request: Request,
    text: str = Form(min_length=1, max_length=600),
    audio: UploadFile = File(),  # noqa: B008
) -> Any:
    return await _assess(request, text, decode_wav(await audio.read()), None)


@router.get("/reference")
async def reference(
    request: Request, phrase_id: str | None = None, text: str | None = None, speed: float = 1.0
) -> Response:
    """Reference reading in the tutor's voice (Kokoro), audio/wav."""
    core = core_of(request)
    spoken = core.pron.phrase_text(phrase_id) if phrase_id else text
    if not spoken or len(spoken) > 600 or has_cyrillic(spoken):
        raise ApiError(404 if phrase_id else 422, "not_found" if phrase_id else "pron_no_text")
    if not 0.5 <= speed <= 1.5:
        raise ApiError(422, "validation")
    settings = core.settings.get()
    result = await core.gpu.run(core.tts.synthesize, spoken, voice=settings.voice, speed=speed)
    return Response(wav_bytes(result.audio, result.sample_rate), media_type="audio/wav")


class Feedback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt_id: str = Field(min_length=1, max_length=64)
    word_index: int = Field(ge=0)
    phone_index: int = Field(ge=0)
    verdict: Literal["was_correct"]


@router.post("/feedback")
async def feedback(request: Request, body: Feedback) -> dict[str, Any]:
    try:
        return core_of(request).pron.feedback(body.attempt_id, body.word_index, body.phone_index)
    except PronError as e:
        raise ApiError(404, "not_found") from e


@router.get("/sounds")
async def sounds(request: Request, days: int = 7) -> list[dict[str, Any]]:
    return core_of(request).pron.sounds(days)
