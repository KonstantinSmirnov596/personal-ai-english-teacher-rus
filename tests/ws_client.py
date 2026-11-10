"""Test-side WebSocket helper speaking the window protocol (text JSON + binary audio)."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from trainer.speech.audio import float_to_pcm16, read_wav


@dataclass
class Received:
    msg: dict[str, Any]
    audio: bytes | None = None
    at: float = field(default_factory=time.perf_counter)


class WSClient:
    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.log: list[Received] = []

    def send(self, msg: dict[str, Any], audio: bytes | None = None) -> None:
        self.ws.send(json.dumps(msg))
        if audio is not None:
            self.ws.send(audio)

    def recv(self, timeout: float = 30.0) -> Received:
        raw = self.ws.recv(timeout=timeout)
        if isinstance(raw, bytes):
            raise AssertionError("unexpected binary frame without announcement")
        msg = json.loads(raw)
        audio = None
        if isinstance(msg.get("audio"), dict):
            data = self.ws.recv(timeout=timeout)
            assert isinstance(data, bytes), "audio message must be followed by a binary frame"
            assert len(data) == msg["audio"]["bytes"]
            audio = data
        item = Received(msg=msg, audio=audio)
        self.log.append(item)
        return item

    def until(self, pred: Any, timeout: float = 60.0) -> list[Received]:
        """Receive until pred(msg) is true; returns everything received including the match."""
        out: list[Received] = []
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                types = [r.msg["type"] for r in out]
                raise TimeoutError(f"condition not met; received {types}")
            try:
                r = self.recv(timeout=left)
            except TimeoutError:
                types = [x.msg.get("type") for x in out]
                raise TimeoutError(f"condition not met; received {types}") from None
            out.append(r)
            if pred(r.msg):
                return out

    def collect(self, seconds: float) -> list[Received]:
        out: list[Received] = []
        deadline = time.monotonic() + seconds
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return out
            try:
                out.append(self.recv(timeout=left))
            except TimeoutError:
                return out

    def send_turn(self, wav: Path, turn_id: str | None = None) -> tuple[str, float]:
        """turn.begin + turn.audio from a WAV file. Returns (turn_id, send time)."""
        audio, rate = read_wav(wav)
        audio = trim_trailing_silence(audio, rate)
        pcm = float_to_pcm16(audio)
        tid = turn_id or str(uuid.uuid4())
        self.send({"type": "turn.begin", "turn_id": tid, "mode": "manual"})
        sent = time.perf_counter()
        self.send(
            {
                "type": "turn.audio",
                "turn_id": tid,
                "audio": {"sample_rate": rate, "format": "pcm_s16le", "bytes": len(pcm)},
            },
            pcm,
        )
        return tid, sent


def trim_trailing_silence(audio: np.ndarray, rate: int, keep_s: float = 0.3) -> np.ndarray:
    loud = np.nonzero(np.abs(audio) > 0.01)[0]
    if len(loud) == 0:
        return audio
    end = min(len(audio), int(loud[-1] + keep_s * rate))
    return audio[:end]
