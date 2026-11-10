"""Corrector: second, independent LLM request per learner turn (docs/prompts/corrector.md)."""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trainer.data_files import pattern_keys
from trainer.llm.client import Endpoint, LLMClient, LLMError
from trainer.prompts.corrector import build_system_prompt, build_user_message

log = logging.getLogger(__name__)

CorrectionType = Literal["grammar", "vocabulary", "phrasing"]
MAX_ITEMS = 3


class RawItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    quote: str = Field(min_length=1)
    fix: str = Field(min_length=1)
    type: str
    note_ru: str = Field(min_length=1)
    pattern: str = "other"


class RawResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[dict[str, Any]] = Field(default_factory=list)
    better: str = ""


@dataclass
class CorrectorResult:
    status: Literal["ok", "empty", "failed", "disabled"]
    items: list[dict[str, Any]] = field(default_factory=list)
    better: str = ""


_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def parse_response(text: str) -> RawResponse | None:
    """Parse the model output; None when empty or not a valid object."""
    if not text or not text.strip():
        return None
    candidate = text.strip()
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        m = _JSON_OBJECT.search(candidate)  # connections without JSON mode may wrap the object
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    try:
        return RawResponse.model_validate(data)
    except ValidationError:
        return None


def locate(quote: str, text: str) -> tuple[int, int] | None:
    """Character span of quote in text: exact first, then ignoring case and repeated spaces."""
    if not quote:
        return None
    idx = text.find(quote)
    if idx >= 0:
        return idx, idx + len(quote)
    norm_chars: list[str] = []
    index_map: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if prev_space:
                continue
            prev_space = True
            norm_chars.append(" ")
        else:
            prev_space = False
            norm_chars.append(ch.lower())
        index_map.append(i)
    norm_text = "".join(norm_chars)
    norm_quote = re.sub(r"\s+", " ", quote.strip()).lower()
    j = norm_text.find(norm_quote)
    if j < 0:
        return None
    start = index_map[j]
    end = index_map[j + len(norm_quote) - 1] + 1
    return start, end


_WORD = re.compile(r"[A-Za-z0-9']+")


def _norm_words(text: str) -> list[str]:
    return [w.lower().strip("'") for w in _WORD.findall(text)]


def _same_after_norm(a: str, b: str) -> bool:
    return _norm_words(a) == _norm_words(b)


def postprocess(
    raw: RawResponse, learner_text: str, unsure_words: list[str]
) -> tuple[list[dict[str, Any]], str]:
    keys = set(pattern_keys())
    unsure = {w.lower().strip("'.,!?") for w in unsure_words if w.strip()}
    items: list[dict[str, Any]] = []
    for entry in raw.items[: MAX_ITEMS * 2]:
        try:
            item = RawItem.model_validate(entry)
        except ValidationError:
            continue
        if item.type not in ("grammar", "vocabulary", "phrasing"):
            continue
        if _same_after_norm(item.quote, item.fix):
            continue
        if unsure & set(_norm_words(item.quote)):
            continue
        span = locate(item.quote, learner_text)
        items.append(
            {
                "id": str(uuid.uuid4()),
                "quote": item.quote,
                "start": span[0] if span else None,
                "end": span[1] if span else None,
                "fix": item.fix,
                "type": item.type,
                "note_ru": item.note_ru.strip(),
                "pattern": item.pattern if item.pattern in keys else "other",
            }
        )
        if len(items) >= MAX_ITEMS:
            break
    better = raw.better.strip() if items else ""
    return items, better


async def run_corrector(
    llm: LLMClient,
    endpoint: Endpoint,
    *,
    level: str,
    tutor_said: str,
    learner_said: str,
    unsure_words: list[str],
) -> CorrectorResult:
    messages = [
        {"role": "system", "content": build_system_prompt(pattern_keys())},
        {
            "role": "user",
            "content": build_user_message(level, tutor_said, learner_said, unsure_words),
        },
    ]
    for attempt in (1, 2):
        try:
            text = await llm.complete(endpoint, messages, json_mode=True, max_tokens=800)
        except LLMError as e:
            log.warning("corrector request failed (attempt %d): %s %s", attempt, e.code, e.detail)
            if not e.retryable:
                return CorrectorResult(status="failed")
            continue
        raw = parse_response(text)
        if raw is None:
            log.warning("corrector returned empty or invalid json (attempt %d)", attempt)
            continue
        items, better = postprocess(raw, learner_said, unsure_words)
        return CorrectorResult(status="ok" if items else "empty", items=items, better=better)
    return CorrectorResult(status="failed")
