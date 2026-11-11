"""JSON request with schema validation and one retry (helpers, summary)."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable

from pydantic import BaseModel, ValidationError

from trainer.llm.client import Endpoint, LLMClient, LLMError

log = logging.getLogger(__name__)

_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
_CYRILLIC = re.compile(r"[Ѐ-ԯ]")


def has_cyrillic(text: str) -> bool:
    return bool(_CYRILLIC.search(text or ""))


def parse_model[M: BaseModel](text: str, model: type[M]) -> M | None:
    if not text or not text.strip():
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = _OBJECT.search(text)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    try:
        return model.model_validate(data)
    except ValidationError:
        return None


async def ask_json[M: BaseModel](
    llm: LLMClient,
    endpoint: Endpoint,
    prompt: str,
    model: type[M],
    *,
    check: Callable[[M], bool] | None = None,
    max_tokens: int = 600,
) -> M:
    """Single user message, JSON mode, schema check, one retry. Raises LLMError on failure."""
    messages = [{"role": "user", "content": prompt}]
    last_error: LLMError | None = None
    for attempt in (1, 2):
        try:
            text = await llm.complete(endpoint, messages, json_mode=True, max_tokens=max_tokens)
        except LLMError as e:
            if not e.retryable:
                raise
            last_error = e
            continue
        parsed = parse_model(text, model)
        if parsed is not None and (check is None or check(parsed)):
            return parsed
        log.warning("invalid json from provider for %s (attempt %d)", model.__name__, attempt)
    raise last_error or LLMError("llm_bad_response", "invalid json after retry")
