"""Audio helpers: PCM conversion, resampling, WAV I/O."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import numpy.typing as npt
import soundfile as sf
import soxr

STT_RATE = 16_000
TTS_RATE = 24_000

FloatAudio = npt.NDArray[np.float32]


def pcm16_to_float(data: bytes) -> FloatAudio:
    """Signed 16-bit little-endian mono PCM -> float32 in [-1, 1]."""
    if len(data) % 2:
        raise ValueError("PCM s16le payload must have an even number of bytes")
    return (np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0).astype(np.float32)


def float_to_pcm16(audio: npt.NDArray[np.floating]) -> bytes:
    clipped = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    return (clipped * 32767.0).round().astype("<i2").tobytes()


def resample(audio: npt.NDArray[np.floating], src_rate: int, dst_rate: int) -> FloatAudio:
    audio32 = np.asarray(audio, dtype=np.float32)
    if src_rate == dst_rate:
        return audio32
    return soxr.resample(audio32, src_rate, dst_rate, quality="HQ").astype(np.float32)


def to_mono(audio: npt.NDArray[np.floating]) -> FloatAudio:
    arr = np.asarray(audio, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr.mean(axis=1)
    return arr.astype(np.float32)


def read_wav(path: Path) -> tuple[FloatAudio, int]:
    data, rate = sf.read(str(path), dtype="float32", always_2d=False)
    return to_mono(data), int(rate)


def write_wav(path: Path, audio: npt.NDArray[np.floating], rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.asarray(audio, dtype=np.float32), rate, subtype="PCM_16")


def wav_bytes(audio: npt.NDArray[np.floating], rate: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.asarray(audio, dtype=np.float32), rate, subtype="PCM_16", format="WAV")
    return buf.getvalue()


def duration_s(audio: npt.NDArray[np.floating], rate: int) -> float:
    return float(len(audio)) / float(rate)
