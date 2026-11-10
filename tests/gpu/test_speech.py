"""Speech services behind the single GPU queue (plan item 1.5)."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from trainer.core import GpuQueue
from trainer.speech.audio import STT_RATE, TTS_RATE, resample
from trainer.speech.stt import SpeechRecognizer
from trainer.speech.tts import CyrillicTextError, SpeechSynthesizer
from trainer.wer import wer

pytestmark = pytest.mark.gpu


@pytest.fixture(scope="module")
def models() -> tuple[SpeechRecognizer, SpeechSynthesizer]:
    stt = SpeechRecognizer()
    tts = SpeechSynthesizer()
    stt.load()
    tts.load()
    return stt, tts


def test_queue_runs_jobs_sequentially(models) -> None:  # noqa: ANN001
    stt, tts = models
    queue = GpuQueue()

    async def scenario() -> list[str]:
        texts = ["The meeting starts at nine.", "Could you share your screen, please?"]
        syn = await asyncio.gather(*(queue.run(tts.synthesize, t) for t in texts))
        heard = await asyncio.gather(
            *(queue.run(stt.transcribe, resample(s.audio, TTS_RATE, STT_RATE)) for s in syn)
        )
        return [h.text for h in heard]

    heard = asyncio.run(scenario())
    queue.shutdown()
    assert wer(["The meeting starts at nine.", "Could you share your screen, please?"], heard) <= 0.1


def test_input_resampled_from_48k(models) -> None:  # noqa: ANN001
    stt, tts = models
    syn = tts.synthesize("I usually work from home on Fridays.")
    audio48 = resample(syn.audio, TTS_RATE, 48_000)
    tr = stt.transcribe(resample(audio48, 48_000, STT_RATE))
    assert wer(["I usually work from home on Fridays."], [tr.text]) <= 0.1


def test_synthesis_rejects_cyrillic(models) -> None:  # noqa: ANN001
    _stt, tts = models
    with pytest.raises(CyrillicTextError):
        tts.synthesize("Привет, это проверка.")
    with pytest.raises(CyrillicTextError):
        tts.synthesize("Hello, мир.")


def test_silence_gives_empty_transcript(models) -> None:  # noqa: ANN001
    stt, _tts = models
    tr = stt.transcribe(np.zeros(STT_RATE * 3, dtype=np.float32))
    assert tr.text == ""
