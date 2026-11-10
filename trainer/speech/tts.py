"""Speech synthesis with Kokoro (American English voices only)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from trainer.model_registry import KOKORO_VOICES, TTS, local_path
from trainer.speech.audio import TTS_RATE, FloatAudio

_CYRILLIC = re.compile(r"[Ѐ-ӿԀ-ԯ]")


class CyrillicTextError(ValueError):
    """Russian text must never reach speech synthesis."""


@dataclass(frozen=True)
class Synthesis:
    audio: FloatAudio
    sample_rate: int
    elapsed_ms: float


class SpeechSynthesizer:
    """Wrapper over ``kokoro.KPipeline`` with ``lang_code='a'`` (American English)."""

    def __init__(self, device: str = "cuda") -> None:
        self.device = device
        self._pipeline: Any = None
        self._voices: dict[str, Any] = {}

    @property
    def loaded(self) -> bool:
        return self._pipeline is not None

    def load(self) -> None:
        if self._pipeline is not None:
            return
        from kokoro import KModel, KPipeline

        root = local_path(TTS)
        model = (
            KModel(
                repo_id=TTS.repo_id,
                config=str(root / "config.json"),
                model=str(root / "kokoro-v1_0.pth"),
            )
            .to(self.device)
            .eval()
        )
        self._pipeline = KPipeline(lang_code="a", repo_id=TTS.repo_id, model=model)
        self._root = root

    def unload(self) -> None:
        self._pipeline = None
        self._voices.clear()

    def _voice_path(self, voice: str) -> str:
        if voice not in KOKORO_VOICES:
            raise ValueError(f"unknown voice: {voice}")
        return str(self._root / "voices" / f"{voice}.pt")

    def synthesize(self, text: str, voice: str = "af_heart", speed: float = 1.0) -> Synthesis:
        if _CYRILLIC.search(text):
            raise CyrillicTextError("Cyrillic text is not allowed in speech synthesis")
        if not 0.5 <= speed <= 2.0:
            raise ValueError("speed must be within 0.5..2.0")
        self.load()
        started = time.perf_counter()
        chunks: list[FloatAudio] = []
        for result in self._pipeline(text, voice=self._voice_path(voice), speed=speed):
            if result.audio is not None:
                chunks.append(result.audio.detach().cpu().numpy().astype(np.float32))
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        return Synthesis(
            audio=audio,
            sample_rate=TTS_RATE,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )
