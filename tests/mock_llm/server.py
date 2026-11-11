"""OpenAI-compatible mock LLM for tests (docs/testing.md, «Мок LLM»).

Routes: ``POST /v1/chat/completions`` (stream or JSON), ``POST /__mock/script``,
``GET /__mock/requests``, ``POST /__mock/reset``. These control routes exist only here.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

DEFAULT_REPLY = (
    "That sounds like a great project. What was the hardest part of it for you? "
    "I'd love to hear more."
)


@dataclass
class Script:
    first_delay_ms: int = 0
    chunk_delay_ms: int = 20
    text: str = DEFAULT_REPLY
    error_status: int | None = None
    error_count: int | None = None  # fail only the first N requests; None = all
    break_after_chunks: int | None = None
    empty: bool = False
    invalid_json: bool = False
    empty_json_count: int = 0  # first N JSON requests return empty content
    hang: bool = False
    corrector_items: list[dict[str, Any]] | None = None


@dataclass
class MockState:
    api_key: str
    script: Script = field(default_factory=Script)
    requests: list[dict[str, Any]] = field(default_factory=list)
    errors_served: int = 0
    empty_json_served: int = 0


def _words_chunks(text: str) -> list[str]:
    words = text.split(" ")
    chunks: list[str] = []
    i = 0
    size = 2
    while i < len(words):
        part = " ".join(words[i : i + size])
        chunks.append(part if i + size >= len(words) else part + " ")
        i += size
        size = 3 if size == 2 else 2
    return chunks


def _learner_said(body: dict[str, Any]) -> str:
    for m in reversed(body.get("messages", [])):
        if m.get("role") == "user":
            try:
                data = json.loads(m.get("content", ""))
            except (json.JSONDecodeError, TypeError):
                return str(m.get("content", ""))
            if isinstance(data, dict):
                return str(data.get("learner_said", ""))
    return ""


def _json_reply(body: dict[str, Any], script: Script) -> str:
    system = " ".join(
        m.get("content", "") for m in body.get("messages", []) if m.get("role") == "system"
    )
    if "corrections as json" in system:
        said = _learner_said(body)
        words = said.split()
        if script.corrector_items is not None:
            items = script.corrector_items
        elif len(words) >= 2:
            quote = " ".join(words[:2])
            items = [
                {
                    "quote": quote,
                    "fix": quote + " indeed",
                    "type": "grammar",
                    "note_ru": "Тестовая правка от мока.",
                    "pattern": "for_since",
                }
            ]
        else:
            items = []
        better = (said + " Indeed.") if items else ""
        return json.dumps({"items": items, "better": better}, ensure_ascii=False)
    if "end-of-session notes" in system:
        return json.dumps(_summary_reply(body), ensure_ascii=False)
    user = " ".join(
        m.get("content", "") for m in body.get("messages", []) if m.get("role") == "user"
    )
    if "Suggest replies" in user:
        return json.dumps(
            {
                "options": [
                    {"en": "Sure, I can tell you more about it."},
                    {"en": "Actually, could we talk about something else?"},
                ]
            }
        )
    if "Translate the sentence into natural Russian" in user:
        return json.dumps({"ru": "Тестовый перевод реплики."}, ensure_ascii=False)
    if "wants to say something in American English" in user:
        return json.dumps(
            {
                "en": "I'd like to ask a question.",
                "alt": "Can I ask you something?",
                "note_ru": "Тестовая заметка.",
            },
            ensure_ascii=False,
        )
    if "Check a spoken answer" in user:
        return json.dumps(
            {"correct": False, "note_ru": "Тестовая проверка: нужен for."}, ensure_ascii=False
        )
    return json.dumps({"ok": True})


def _summary_reply(body: dict[str, Any]) -> dict[str, Any]:
    data = json.loads(body["messages"][-1]["content"])
    lines: list[str] = data.get("tutor_lines", [])
    phrases = []
    if lines:
        words = lines[0].rstrip(".?!").split()
        if len(words) >= 3:
            phrases.append(
                {"en": " ".join(words[-3:]), "ru": "тестовая фраза", "context": lines[0]}
            )
    if not data.get("focus_pattern"):
        return {"key_takeaway": None, "exercise": None, "phrases": phrases}
    examples = [
        {"wrong": e["quote"], "right": e["fix"]} for e in data.get("focus_examples", [])[:2]
    ]
    return {
        "key_takeaway": {
            "title": "for / since",
            "explanation_ru": "For ставится с длительностью, since — с моментом начала.",
            "examples": examples,
        },
        "exercise": {
            "prompt_ru": "Я работаю здесь три года.",
            "answer_en": "I have worked here for three years.",
            "accept": [
                "I've worked here for three years.",
                "I've been working here for three years.",
            ],
        },
        "phrases": phrases,
    }


def build_app(state: MockState) -> FastAPI:
    app = FastAPI()

    @app.post("/__mock/script")
    async def set_script(request: Request) -> dict[str, Any]:
        data = await request.json()
        state.script = Script(**data)
        state.errors_served = 0
        state.empty_json_served = 0
        return asdict(state.script)

    @app.post("/__mock/reset")
    async def reset() -> dict[str, Any]:
        state.script = Script()
        state.requests.clear()
        state.errors_served = 0
        state.empty_json_served = 0
        return {"ok": True}

    @app.get("/__mock/requests")
    async def get_requests() -> list[dict[str, Any]]:
        return state.requests

    @app.post("/v1/chat/completions")
    async def completions(request: Request) -> Response:
        body = await request.json()
        auth = request.headers.get("authorization", "")
        record: dict[str, Any] = {"body": body, "authorization": auth, "at": time.time()}
        state.requests.append(record)
        if auth != f"Bearer {state.api_key}":
            return JSONResponse({"error": {"message": "Authentication Fails"}}, status_code=401)
        s = state.script
        if s.error_status is not None and (
            s.error_count is None or state.errors_served < s.error_count
        ):
            state.errors_served += 1
            return JSONResponse({"error": {"message": f"mock {s.error_status}"}}, s.error_status)
        if s.hang:
            await asyncio.sleep(3600)
        if s.first_delay_ms:
            await asyncio.sleep(s.first_delay_ms / 1000)

        if body.get("stream"):

            async def events() -> AsyncIterator[bytes]:
                # stream_state: "open" -> "done" (sent [DONE]) | "closed_by_client"
                record["stream_state"] = "open"
                try:
                    yield b": keep-alive\n\n"
                    if not s.empty:
                        for n, chunk in enumerate(_words_chunks(s.text)):
                            if s.break_after_chunks is not None and n >= s.break_after_chunks:
                                record["stream_state"] = "broken"
                                return  # connection ends without [DONE]
                            payload = {
                                "id": "mock",
                                "object": "chat.completion.chunk",
                                "choices": [{"index": 0, "delta": {"content": chunk}}],
                            }
                            yield f"data: {json.dumps(payload)}\n\n".encode()
                            record["chunks_sent"] = n + 1
                            if s.chunk_delay_ms:
                                await asyncio.sleep(s.chunk_delay_ms / 1000)
                    yield b"data: [DONE]\n\n"
                    record["stream_state"] = "done"
                except (asyncio.CancelledError, GeneratorExit):
                    record["stream_state"] = "closed_by_client"
                    raise

            return StreamingResponse(events(), media_type="text/event-stream")

        if body.get("response_format"):
            if s.invalid_json:
                content = "{not json"
            elif s.empty or state.empty_json_served < s.empty_json_count:
                state.empty_json_served += 1
                content = "  "
            else:
                content = _json_reply(body, s)
        else:
            content = "" if s.empty else s.text
        return JSONResponse(
            {
                "id": "mock",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
            }
        )

    return app


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class MockLLM:
    """Runs the mock in a background thread on a free 127.0.0.1 port."""

    def __init__(self, api_key: str) -> None:
        self.state = MockState(api_key=api_key)
        self.port = _free_port()
        config = uvicorn.Config(
            build_app(self.state),
            host="127.0.0.1",
            port=self.port,
            log_config=None,
            access_log=False,
            lifespan="off",
            timeout_graceful_shutdown=1,
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def start(self) -> MockLLM:
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise TimeoutError("mock LLM did not start")
            time.sleep(0.02)
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)

    def script(self, **kwargs: Any) -> None:
        self.state.script = Script(**kwargs)
        self.state.errors_served = 0
        self.state.empty_json_served = 0

    def reset(self) -> None:
        self.state.script = Script()
        self.state.requests.clear()
        self.state.errors_served = 0
        self.state.empty_json_served = 0

    @property
    def requests(self) -> list[dict[str, Any]]:
        return self.state.requests
