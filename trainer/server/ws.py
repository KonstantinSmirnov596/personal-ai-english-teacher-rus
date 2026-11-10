"""WebSocket endpoint: framing (text JSON + following binary audio) and dispatch."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from pydantic import BaseModel, ValidationError
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from trainer.conversation import Conversation
from trainer.core import Core
from trainer.i18n_ru import error_text
from trainer.protocol import MAX_BINARY_FRAME, MAX_TEXT_FRAME, ErrorMsg, parse_incoming

log = logging.getLogger(__name__)

CLOSE_REPLACED = 4001


class Sender:
    """Serializes outgoing frames so a text message and its audio frame stay adjacent."""

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self._lock = asyncio.Lock()
        self.closed = False

    async def send(self, msg: BaseModel, audio: bytes | None = None) -> None:
        if self.closed:
            return
        async with self._lock:
            try:
                await self.ws.send_text(msg.model_dump_json())
                if audio is not None:
                    await self.ws.send_bytes(audio)
            except (WebSocketDisconnect, RuntimeError, ConnectionError):
                self.closed = True


async def websocket_endpoint(ws: WebSocket) -> None:
    core: Core = ws.app.state.core
    previous = core.current_ws
    if previous is not None and previous.application_state == WebSocketState.CONNECTED:
        with contextlib.suppress(RuntimeError):
            await previous.close(code=CLOSE_REPLACED)
    await ws.accept()
    core.current_ws = ws
    core.ws_count += 1
    core.ws_ever_connected = True
    core.ws_last_change = time.monotonic()
    sender = Sender(ws)
    conv = Conversation(core, sender)
    try:
        await conv.send_hello()
        await _receive_loop(ws, conv, sender)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("websocket loop failed")
    finally:
        sender.closed = True
        await conv.close()
        core.ws_count -= 1
        core.ws_last_change = time.monotonic()
        if core.current_ws is ws:
            core.current_ws = None


async def _receive_loop(ws: WebSocket, conv: Conversation, sender: Sender) -> None:
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(message.get("code", 1000))
        text = message.get("text")
        if text is None:
            # A binary frame is only valid right after a message that announced it.
            await _bad_request(sender, "unexpected binary frame")
            continue
        if len(text.encode("utf-8")) > MAX_TEXT_FRAME:
            await _bad_request(sender, "text frame too large")
            continue
        try:
            msg = parse_incoming(text)
        except ValidationError as e:
            await _bad_request(sender, f"invalid message: {e.error_count()} errors")
            continue
        audio: bytes | None = None
        meta: Any = getattr(msg, "audio", None)
        if meta is not None:
            frame = await ws.receive()
            if frame["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(frame.get("code", 1000))
            data = frame.get("bytes")
            if data is None or len(data) != meta.bytes or len(data) > MAX_BINARY_FRAME:
                await _bad_request(sender, "audio frame missing or size mismatch")
                continue
            audio = data
        await conv.handle(msg, audio)


async def _bad_request(sender: Sender, detail: str) -> None:
    log.warning("bad websocket request: %s", detail)
    await sender.send(
        ErrorMsg(code="bad_request", message_ru=error_text("bad_request"), retryable=False)
    )
