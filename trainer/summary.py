"""Session summary (docs/prompts/summary.md): data from the database plus one LLM request."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trainer.core import NoActiveConnection
from trainer.llm.client import LLMError
from trainer.llm.json_call import has_cyrillic, parse_model
from trainer.patterns import IGNORED, update_after_session
from trainer.prompts.summary import SYSTEM

log = logging.getLogger(__name__)


class Example(BaseModel):
    model_config = ConfigDict(extra="ignore")
    wrong: str
    right: str


class KeyTakeaway(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = Field(min_length=1, max_length=120)
    explanation_ru: str = Field(min_length=1, max_length=1000)
    examples: list[Example] = Field(default_factory=list, max_length=4)


class Exercise(BaseModel):
    model_config = ConfigDict(extra="ignore")
    prompt_ru: str = Field(min_length=1, max_length=300)
    answer_en: str = Field(min_length=1, max_length=300)
    accept: list[str] = Field(default_factory=list, max_length=6)


class Phrase(BaseModel):
    model_config = ConfigDict(extra="ignore")
    en: str = Field(min_length=1, max_length=120)
    ru: str = Field(default="", max_length=200)
    context: str = Field(default="", max_length=600)


class SummaryOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    key_takeaway: KeyTakeaway | None = None
    exercise: Exercise | None = None
    phrases: list[Phrase] = Field(default_factory=list, max_length=8)


def phrase_in_lines(phrase: str, lines: list[str]) -> str | None:
    """Return the tutor line containing the phrase (case-insensitive; the first word may differ
    in form, e.g. "walk me through" vs "walked me through")."""
    words = re.findall(r"[a-z']+", phrase.lower())
    if not words:
        return None
    first = re.escape(words[0][: max(3, len(words[0]) - 3)])
    rest = r"\W+".join(re.escape(w) for w in words[1:])
    pattern = re.compile(r"\b" + first + r"[a-z']*" + (r"\W+" + rest if rest else "") + r"\b")
    for line in lines:
        if pattern.search(line.lower()):
            return line
    return None


def choose_focus(patterns: list[dict[str, Any]]) -> str | None:
    seen = [p for p in patterns if p["count_in_session"] > 0 and p["key"] not in IGNORED]
    if not seen:
        return None
    seen.sort(key=lambda p: (p["count_in_session"], p["session_streak"]), reverse=True)
    return str(seen[0]["key"])


def _validated(out: SummaryOut, tutor_lines: list[str], focus: str | None) -> dict[str, Any]:
    takeaway = out.key_takeaway if focus else None
    exercise = out.exercise if focus else None
    if takeaway and has_cyrillic(takeaway.title):
        takeaway = None
    if exercise and (
        has_cyrillic(exercise.answer_en) or any(has_cyrillic(a) for a in exercise.accept)
    ):
        exercise = None
    phrases: list[dict[str, Any]] = []
    for p in out.phrases[:4]:
        if has_cyrillic(p.en) or has_cyrillic(p.context):
            continue
        line = phrase_in_lines(p.en, tutor_lines)
        if line is None:
            continue
        phrases.append({"en": p.en, "ru": p.ru, "context": line})
    return {
        "key_takeaway": takeaway.model_dump() if takeaway else None,
        "exercise": exercise.model_dump() if exercise else None,
        "phrases": phrases,
    }


async def build_summary(core: Any, session: Any) -> dict[str, Any]:
    corrections = [
        {
            "id": c["id"],
            "turn_id": c["turn_id"],
            "quote": c["quote"],
            "fix": c["fix"],
            "type": c["type"],
            "note_ru": c["note_ru"],
            "pattern": c["pattern"],
        }
        for c in core.sessions.corrections(session.id)
    ]
    patterns = update_after_session(core, session.id)
    focus = choose_focus(patterns)
    tutor_lines = [
        t["text"] for t in core.sessions.turns(session.id) if t["role"] == "tutor" and t["text"]
    ]
    llm_part: dict[str, Any] = {"key_takeaway": None, "exercise": None, "phrases": []}
    if tutor_lines or focus:
        examples = [
            {"quote": c["quote"], "fix": c["fix"]} for c in corrections if c["pattern"] == focus
        ]
        user = json.dumps(
            {
                "level": session.level,
                "focus_pattern": focus,
                "focus_examples": examples[:4],
                "tutor_lines": tutor_lines[-30:],
            },
            ensure_ascii=False,
        )
        try:
            _row, endpoint = core.active_endpoint()
            messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
            for attempt in (1, 2):
                text = await core.llm.complete(endpoint, messages, json_mode=True, max_tokens=1200)
                parsed = parse_model(text, SummaryOut)
                if parsed is not None:
                    llm_part = _validated(parsed, tutor_lines, focus)
                    break
                log.warning("summary json invalid (attempt %d)", attempt)
        except (NoActiveConnection, LLMError, ValidationError) as e:
            log.warning("summary request failed: %s", e)
    if llm_part["key_takeaway"] and focus:
        llm_part["key_takeaway"]["pattern"] = focus
        llm_part["key_takeaway"]["count_in_session"] = next(
            (p["count_in_session"] for p in patterns if p["key"] == focus), 0
        )
    return {
        "key_takeaway": llm_part["key_takeaway"],
        "exercise": llm_part["exercise"],
        "corrections": corrections,
        "patterns": patterns,
        "sounds": [],
        "phrases": llm_part["phrases"],
    }
