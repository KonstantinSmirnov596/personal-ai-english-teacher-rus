"""Speech recognition with faster-whisper (large-v3-turbo, float16 on CUDA)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from trainer.gpu_env import prepare_cuda_dlls
from trainer.model_registry import STT, local_path
from trainer.speech.audio import STT_RATE, FloatAudio

# Words recognized with lower probability than this are reported as low-confidence.
LOW_CONF_THRESHOLD = 0.6


@dataclass(frozen=True)
class Word:
    w: str
    start: float
    end: float
    p: float


@dataclass(frozen=True)
class Transcript:
    text: str
    words: list[Word] = field(default_factory=list)
    duration_s: float = 0.0
    elapsed_ms: float = 0.0

    @property
    def low_conf(self) -> list[int]:
        return [i for i, w in enumerate(self.words) if w.p < LOW_CONF_THRESHOLD]


class SpeechRecognizer:
    """Thin wrapper over ``faster_whisper.WhisperModel`` with fixed English decoding."""

    def __init__(
        self,
        device: str = "cuda",
        compute_type: str = "float16",
        beam_size: int = 1,
        vad_filter: bool = True,
    ) -> None:
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self._model: Any = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self._model is not None:
            return
        prepare_cuda_dlls()
        from faster_whisper import WhisperModel

        self._model = WhisperModel(
            str(local_path(STT)), device=self.device, compute_type=self.compute_type
        )

    def unload(self) -> None:
        self._model = None

    def transcribe(self, audio_16k: FloatAudio) -> Transcript:
        """Transcribe mono float32 audio at 16 kHz."""
        self.load()
        audio = np.asarray(audio_16k, dtype=np.float32)
        started = time.perf_counter()
        segments, _info = self._model.transcribe(
            audio,
            language="en",
            task="transcribe",
            beam_size=self.beam_size,
            word_timestamps=True,
            vad_filter=self.vad_filter,
            condition_on_previous_text=False,
        )
        words: list[Word] = []
        texts: list[str] = []
        for seg in segments:
            texts.append(seg.text.strip())
            for w in seg.words or []:
                token = w.word.strip()
                if token:
                    words.append(
                        Word(
                            w=token, start=float(w.start), end=float(w.end), p=float(w.probability)
                        )
                    )
        elapsed = (time.perf_counter() - started) * 1000.0
        return Transcript(
            text=" ".join(t for t in texts if t).strip(),
            words=words,
            duration_s=len(audio) / STT_RATE,
            elapsed_ms=elapsed,
        )
