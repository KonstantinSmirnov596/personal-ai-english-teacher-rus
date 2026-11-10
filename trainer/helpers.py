"""Text helpers (hint, translate, how to say, replay, exercise): implemented in stage 2.3."""

from __future__ import annotations

from typing import Any


async def handle_helper(conv: Any, msg: Any, audio: bytes | None) -> None:
    request_id = getattr(msg, "request_id", None)
    turn_id = getattr(msg, "turn_id", None)
    await conv.error("bad_request", request_id=request_id, turn_id=turn_id)
