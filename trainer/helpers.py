"""Text helpers (docs/prompts/helpers.md): hint, translate, how to say, replay, exercise check.

Helpers never add to the conversation history and never change the core state. Russian text is
returned only as text; English fields are checked for Cyrillic.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from trainer.core import NoActiveConnection
from trainer.llm.client import LLMError
from trainer.llm.json_call import ask_json, has_cyrillic
from trainer.prompts import helpers as prompts
from trainer.protocol import (
    AudioOut,
    ExerciseAnswer,
    HelperHint,
    HelperHowToSay,
    HelperResult,
    HelperTranslate,
    TutorReplay,
    TutorSentence,
)
from trainer.speech.audio import STT_RATE, TTS_RATE, float_to_pcm16, pcm16_to_float, resample
from trainer.speech.sentences import SentenceSplitter, clean_for_speech

log = logging.getLogger(__name__)


class HintOption(BaseModel):
    model_config = ConfigDict(extra="ignore")
    en: str = Field(min_length=1, max_length=300)


class HintOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    options: list[HintOption] = Field(min_length=1, max_length=5)


class TranslateOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ru: str = Field(min_length=1, max_length=1000)


class HowToSayOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    en: str = Field(min_length=1, max_length=400)
    alt: str = Field(default="", max_length=400)
    note_ru: str = Field(default="", max_length=400)


class ExerciseCheckOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    correct: bool
    note_ru: str = Field(default="", max_length=400)


def _hint_ok(h: HintOut) -> bool:
    return all(not has_cyrillic(o.en) for o in h.options)


def _how_ok(h: HowToSayOut) -> bool:
    return not has_cyrillic(h.en) and not has_cyrillic(h.alt)


_CONTRACTIONS = {
    "i'm": "i am",
    "i've": "i have",
    "i'll": "i will",
    "i'd": "i would",
    "you're": "you are",
    "we're": "we are",
    "they're": "they are",
    "he's": "he is",
    "she's": "she is",
    "it's": "it is",
    "that's": "that is",
    "there's": "there is",
    "don't": "do not",
    "doesn't": "does not",
    "didn't": "did not",
    "isn't": "is not",
    "aren't": "are not",
    "wasn't": "was not",
    "weren't": "were not",
    "haven't": "have not",
    "hasn't": "has not",
    "won't": "will not",
    "can't": "cannot",
    "we've": "we have",
    "they've": "they have",
    "you've": "you have",
}


def normalize_answer(text: str) -> str:
    t = text.lower().replace("’", "'")
    words = re.findall(r"[a-z0-9']+", t)
    out: list[str] = []
    for w in words:
        out.extend(_CONTRACTIONS.get(w, w).split())
    return " ".join(x.strip("'") for x in out)


def answer_matches(heard: str, answers: list[str]) -> bool:
    h = normalize_answer(heard)
    return bool(h) and any(h == normalize_answer(a) for a in answers if a)


async def handle_helper(conv: Any, msg: Any, audio: bytes | None) -> None:
    try:
        if isinstance(msg, TutorReplay):
            await replay(conv, msg)
        elif isinstance(msg, ExerciseAnswer):
            assert audio is not None
            await exercise_answer(conv, msg, audio)
        elif isinstance(msg, (HelperHint, HelperTranslate, HelperHowToSay)):
            await text_helper(conv, msg)
    except NoActiveConnection:
        await conv.error("no_active_connection", request_id=getattr(msg, "request_id", None))
    except LLMError as e:
        await conv.error(e.code, request_id=getattr(msg, "request_id", None), retryable=e.retryable)


def _history_lines(conv: Any, limit: int = 4) -> list[str]:
    session = conv.session
    if session is None:
        return []
    lines = []
    for m in session.history[-limit:]:
        who = "Tutor" if m["role"] == "assistant" else "Learner"
        text = m["content"].split("\n[asr_unsure:")[0]
        lines.append(f"{who}: {text}")
    return lines


async def text_helper(conv: Any, msg: Any) -> None:
    core = conv.core
    _row, endpoint = core.active_endpoint()
    level = conv.session.level if conv.session else core.settings.get().level
    if isinstance(msg, HelperHint):
        scenario = conv.session.scenario if conv.session else {}
        prompt = prompts.HINT.format(
            level=level,
            scenario_title_en=scenario.get("title_en") or scenario.get("role", ""),
            last_messages="\n".join(_history_lines(conv)),
        )
        hint = await ask_json(core.llm, endpoint, prompt, HintOut, check=_hint_ok)
        data: dict[str, Any] = {"options": [{"en": o.en} for o in hint.options[:3]]}
        kind = "hint"
    elif isinstance(msg, HelperTranslate):
        row = core.sessions.get_turn(msg.turn_id)
        if row is None or row["role"] != "tutor" or not row["text"]:
            await conv.error("bad_request", request_id=msg.request_id)
            return
        out = await ask_json(
            core.llm, endpoint, prompts.TRANSLATE.format(tutor_text=row["text"]), TranslateOut
        )
        data = {"ru": out.ru, "turn_id": msg.turn_id}
        kind = "translate"
    else:
        tutor_text = conv.session.last_tutor_text if conv.session else ""
        prompt = prompts.HOW_TO_SAY.format(level=level, tutor_text=tutor_text, text_ru=msg.text_ru)
        how = await ask_json(core.llm, endpoint, prompt, HowToSayOut, check=_how_ok)
        data = {"en": how.en, "alt": how.alt, "note_ru": how.note_ru}
        kind = "how_to_say"
    await conv.send(HelperResult(request_id=msg.request_id, kind=kind, data=data))  # type: ignore[arg-type]


async def replay(conv: Any, msg: TutorReplay) -> None:
    """Synthesize a tutor turn again (LLM not involved); cached per session."""
    core = conv.core
    row = core.sessions.get_turn(msg.turn_id)
    if row is None or row["role"] != "tutor" or not row["text"]:
        await conv.error("bad_request", turn_id=msg.turn_id)
        return
    settings = core.settings.get()
    speed = round(max(0.5, min(2.0, settings.speed * msg.speed)), 2)
    key = (msg.turn_id, speed, settings.voice)
    cache: dict[Any, list[tuple[str, bytes]]] = conv.replay_cache
    if key not in cache:
        splitter = SentenceSplitter()
        sentences = [*splitter.feed(row["text"]), *splitter.flush()]
        parts: list[tuple[str, bytes]] = []
        for sentence in sentences:
            spoken = clean_for_speech(sentence)
            if not spoken or has_cyrillic(spoken):
                continue
            result = await core.gpu.run(
                core.tts.synthesize, spoken, voice=settings.voice, speed=speed
            )
            parts.append((sentence, float_to_pcm16(result.audio)))
        cache[key] = parts
    for index, (sentence, pcm) in enumerate(cache[key]):
        await conv.send(
            TutorSentence(
                turn_id=msg.turn_id,
                index=index,
                text=sentence,
                replay=True,
                audio=AudioOut(sample_rate=TTS_RATE, bytes=len(pcm)),
            ),
            pcm,
        )


async def exercise_answer(conv: Any, msg: ExerciseAnswer, pcm: bytes) -> None:
    """Spoken answer to the summary exercise: exact match after normalization, else the LLM."""
    core = conv.core
    exercise = (conv.last_summary or {}).get("exercise")
    if not exercise:
        await conv.error("bad_request", request_id=msg.request_id)
        return
    audio = resample(pcm16_to_float(pcm), msg.audio.sample_rate, STT_RATE)
    try:
        transcript = await core.gpu.run(core.stt.transcribe, audio)
    except Exception:
        log.exception("speech recognition failed for exercise answer")
        await conv.error("stt_failed", request_id=msg.request_id, retryable=True)
        return
    heard = transcript.text.strip()
    answers = [exercise["answer_en"], *exercise.get("accept", [])]
    if answer_matches(heard, answers):
        result = {
            "correct": True,
            "note_ru": "",
            "answer_en": exercise["answer_en"],
            "heard": heard,
        }
    elif not heard:
        result = {"correct": False, "note_ru": "", "answer_en": exercise["answer_en"], "heard": ""}
    else:
        _row, endpoint = core.active_endpoint()
        prompt = prompts.EXERCISE_CHECK.format(
            prompt_ru=exercise["prompt_ru"],
            answer_en=exercise["answer_en"],
            focus_title=(conv.last_summary.get("key_takeaway") or {}).get("title", ""),
            learner_said=heard,
        )
        check = await ask_json(core.llm, endpoint, prompt, ExerciseCheckOut)
        result = {
            "correct": check.correct,
            "note_ru": check.note_ru,
            "answer_en": exercise["answer_en"],
            "heard": heard,
        }
    await conv.send(HelperResult(request_id=msg.request_id, kind="exercise", data=result))
