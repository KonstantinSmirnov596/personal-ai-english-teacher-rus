"""OpenAI-compatible chat client: ``POST {base_url}/chat/completions``, streaming over SSE.

The only outbound traffic of the app. HTTPS is mandatory except for 127.0.0.1/localhost;
environment proxies are ignored so requests go exactly to the configured host.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from trainer.logging_setup import redact

log = logging.getLogger(__name__)

FORBIDDEN_EXTRA_KEYS = frozenset({"model", "messages", "stream"})
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})


class LLMError(Exception):
    """Provider failure mapped to a protocol error code."""

    def __init__(self, code: str, detail: str = "", status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = redact(detail)[:200]

    @property
    def retryable(self) -> bool:
        return self.code in {"llm_rate_limited", "llm_unreachable", "llm_bad_response"}


@dataclass(frozen=True)
class Endpoint:
    base_url: str
    model: str
    api_key: str = field(repr=False)
    extra_params: dict[str, Any] = field(default_factory=dict)
    json_mode: bool = True

    @property
    def url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"


def validate_base_url(url: str) -> bool:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and parts.hostname in LOCAL_HOSTS


def validate_extra_params(extra: Any) -> bool:
    return isinstance(extra, dict) and not (FORBIDDEN_EXTRA_KEYS & set(extra))


def map_status(status: int) -> str:
    if status in (401, 402, 403):
        return "llm_unauthorized"
    if status == 429:
        return "llm_rate_limited"
    return "llm_bad_response"


@dataclass
class Timeouts:
    connect: float = 10.0
    read: float = 30.0
    total: float = 120.0


class LLMClient:
    def __init__(self, timeouts: Timeouts | None = None) -> None:
        self.timeouts = timeouts or Timeouts()
        t = self.timeouts
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=t.connect, read=t.read, write=10.0, pool=10.0),
            trust_env=False,
            follow_redirects=False,
            headers={"User-Agent": "trainer/1"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _body(
        self,
        ep: Endpoint,
        messages: list[dict[str, str]],
        *,
        stream: bool,
        json_mode: bool,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"model": ep.model, "messages": messages, "stream": stream}
        if json_mode and ep.json_mode:
            body["response_format"] = {"type": "json_object"}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        for k, v in ep.extra_params.items():
            if k not in FORBIDDEN_EXTRA_KEYS:
                body[k] = v
        return body

    def _headers(self, ep: Endpoint) -> dict[str, str]:
        return {"Authorization": f"Bearer {ep.api_key}", "Content-Type": "application/json"}

    async def stream_chat(
        self,
        ep: Endpoint,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Yield content fragments as they arrive. Raises LLMError."""
        if not validate_base_url(ep.base_url):
            raise LLMError("llm_unreachable", "invalid base URL")
        body = self._body(ep, messages, stream=True, json_mode=False, max_tokens=max_tokens)
        deadline = time.monotonic() + self.timeouts.total
        got_done = False
        try:
            async with self._client.stream(
                "POST", ep.url, json=body, headers=self._headers(ep)
            ) as resp:
                if resp.status_code != 200:
                    text = (await resp.aread()).decode("utf-8", "replace")
                    raise LLMError(map_status(resp.status_code), text, resp.status_code)
                async for line in resp.aiter_lines():
                    if time.monotonic() > deadline:
                        raise LLMError("llm_unreachable", "total timeout")
                    if not line or line.startswith(":"):
                        continue  # keep-alive comments and event separators
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        got_done = True
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError as e:
                        raise LLMError("llm_bad_response", "malformed SSE chunk") from e
                    if "error" in chunk:
                        raise LLMError("llm_bad_response", json.dumps(chunk["error"])[:200])
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        content = delta.get("content")
                        if content:
                            yield content
        except LLMError:
            raise
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as e:
            raise LLMError("llm_unreachable", type(e).__name__) from e
        except httpx.HTTPError as e:
            raise LLMError("llm_bad_response", type(e).__name__) from e
        if not got_done:
            raise LLMError("llm_bad_response", "stream ended without [DONE]")

    async def complete(
        self,
        ep: Endpoint,
        messages: list[dict[str, str]],
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> str:
        """Non-streaming request; returns the message content (may be empty)."""
        if not validate_base_url(ep.base_url):
            raise LLMError("llm_unreachable", "invalid base URL")
        body = self._body(ep, messages, stream=False, json_mode=json_mode, max_tokens=max_tokens)
        try:
            resp = await self._client.post(ep.url, json=body, headers=self._headers(ep))
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as e:
            raise LLMError("llm_unreachable", type(e).__name__) from e
        except httpx.HTTPError as e:
            raise LLMError("llm_bad_response", type(e).__name__) from e
        if resp.status_code != 200:
            raise LLMError(map_status(resp.status_code), resp.text, resp.status_code)
        try:
            payload = resp.json()
            content = payload["choices"][0]["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise LLMError("llm_bad_response", "unexpected response shape") from e
        return str(content)

    async def test(self, ep: Endpoint) -> tuple[bool, int | None, str, str | None]:
        """Short request with latency. Returns (ok, latency_ms, detail, error_code)."""
        started = time.perf_counter()
        try:
            await self.complete(
                ep, [{"role": "user", "content": "Reply with the single word: ok"}], max_tokens=5
            )
        except LLMError as e:
            status = f"{e.status}: " if e.status else ""
            return False, None, f"{status}{e.detail}".strip(), e.code
        latency = int((time.perf_counter() - started) * 1000)
        return True, latency, "", None
