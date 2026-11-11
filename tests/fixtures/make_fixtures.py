"""Synthesizes speech fixtures with Kokoro.

Files go to ``tests/fixtures/audio/generated/`` as 48 kHz mono PCM16 WAV (the fake-microphone
format). A file is created only when it does not exist yet.

Usage: python tests/fixtures/make_fixtures.py [loop|turns|all]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

FIXTURES = Path(__file__).resolve().parent
GENERATED = FIXTURES / "audio" / "generated"
PHRASES = FIXTURES / "phrases.json"
OUT_RATE = 48_000


def load_spec() -> dict:
    return json.loads(PHRASES.read_text(encoding="utf-8"))


def loop_items() -> list[tuple[str, str]]:
    """(fixture name, reference text) for the TTS -> STT loop."""
    spec = load_spec()["loop"]
    return [(f"loop_{i:02d}", text) for i, text in enumerate(spec["phrases"], start=1)]


class _Synth:
    def __init__(self) -> None:
        self._tts = None

    def say(self, text: str, voice: str, speed: float = 1.0) -> np.ndarray:
        if self._tts is None:
            from trainer.speech.tts import SpeechSynthesizer

            self._tts = SpeechSynthesizer()
        from trainer.speech.audio import resample

        res = self._tts.synthesize(text, voice=voice, speed=speed)
        return resample(res.audio, res.sample_rate, OUT_RATE)


def _pink_noise(seconds: float, level_db: float, seed: int = 7) -> np.ndarray:
    n = int(seconds * OUT_RATE)
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(n)
    spectrum = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n, d=1.0 / OUT_RATE)
    freqs[0] = freqs[1]
    pink = np.fft.irfft(spectrum / np.sqrt(freqs), n=n)
    pink /= np.sqrt(np.mean(pink**2))
    return (pink * 10 ** (level_db / 20.0)).astype(np.float32)


def trim_silence(audio: np.ndarray, threshold: float = 0.01, margin_s: float = 0.03) -> np.ndarray:
    loud = np.nonzero(np.abs(audio) > threshold)[0]
    if len(loud) == 0:
        return audio
    margin = int(margin_s * OUT_RATE)
    return audio[max(0, loud[0] - margin) : min(len(audio), loud[-1] + margin)]


def _write(name: str, audio: np.ndarray) -> Path:
    from trainer.speech.audio import write_wav

    path = GENERATED / f"{name}.wav"
    write_wav(path, audio, OUT_RATE)
    return path


def ensure_loop(synth: _Synth | None = None) -> list[tuple[Path, str]]:
    synth = synth or _Synth()
    voice = load_spec()["loop"]["voice"]
    out: list[tuple[Path, str]] = []
    for name, text in loop_items():
        path = GENERATED / f"{name}.wav"
        if not path.exists():
            _write(name, synth.say(text, voice))
        out.append((path, text))
    return out


def ensure_turns(synth: _Synth | None = None) -> dict[str, Path]:
    synth = synth or _Synth()
    out: dict[str, Path] = {}
    for name, spec in load_spec()["turns"].items():
        path = GENERATED / f"{name}.wav"
        out[name] = path
        if path.exists():
            continue
        parts: list[np.ndarray] = []
        for seg in spec["segments"]:
            if "say" in seg:
                # Kokoro pads speech with ~0.4 s of silence; trim it so pauses are exact.
                audio = trim_silence(synth.say(seg["say"], spec["voice"]))
                if "max_s" in seg:
                    audio = audio[: int(seg["max_s"] * OUT_RATE)]
                parts.append(audio)
            elif "silence_s" in seg:
                parts.append(np.zeros(int(seg["silence_s"] * OUT_RATE), dtype=np.float32))
            elif "pink_noise_s" in seg:
                parts.append(_pink_noise(seg["pink_noise_s"], seg["level_db"]))
        _write(name, np.concatenate(parts))
    return out


def main(argv: list[str]) -> int:
    what = argv[1] if len(argv) > 1 else "all"
    synth = _Synth()
    if what in ("loop", "all"):
        for path, _ in ensure_loop(synth):
            print(path)
    if what in ("turns", "all"):
        for path in ensure_turns(synth).values():
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
