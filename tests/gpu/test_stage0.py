"""Stage 0 hardware acceptance: CUDA device and the TTS -> STT loop."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.gpu


def test_cuda_device_is_rtx5060() -> None:
    import torch

    assert torch.cuda.is_available()
    assert "RTX 5060" in torch.cuda.get_device_name(0)


def test_tts_stt_loop_wer() -> None:
    """Kokoro reads 10 phrases, faster-whisper transcribes them: WER must be <= 10 %."""
    from tests.fixtures.make_fixtures import ensure_loop
    from trainer.speech.audio import STT_RATE, read_wav, resample
    from trainer.speech.stt import SpeechRecognizer
    from trainer.wer import wer

    items = ensure_loop()
    stt = SpeechRecognizer()
    refs: list[str] = []
    hyps: list[str] = []
    for path, text in items:
        audio, rate = read_wav(path)
        hyps.append(stt.transcribe(resample(audio, rate, STT_RATE)).text)
        refs.append(text)
    rate = wer(refs, hyps)
    print(f"loop WER: {rate:.3f}")
    assert len(refs) == 10
    assert rate <= 0.10, list(zip(refs, hyps, strict=True))


def test_word_probabilities_present() -> None:
    """Item 0.5: faster-whisper returns a probability per word with word_timestamps=True."""
    from tests.fixtures.make_fixtures import ensure_loop
    from trainer.speech.audio import STT_RATE, read_wav, resample
    from trainer.speech.stt import SpeechRecognizer

    path, _text = ensure_loop()[1]
    audio, rate = read_wav(path)
    tr = SpeechRecognizer().transcribe(resample(audio, rate, STT_RATE))
    assert len(tr.words) >= 5
    assert all(0.0 <= w.p <= 1.0 for w in tr.words)
    assert all(w.end >= w.start for w in tr.words)
    assert sum(w.p for w in tr.words) / len(tr.words) > 0.8


def test_espeak_symbols_in_phoneme_vocab() -> None:
    """Item 0.7: every espeak-ng en-us symbol of the probe words exists in the model vocabulary."""
    import json
    from pathlib import Path

    from trainer.model_registry import PHONEME, local_path
    from trainer.pron.espeak import word_phones

    vocab = json.loads((local_path(PHONEME) / "vocab.json").read_text(encoding="utf-8"))
    data_file = Path(__file__).resolve().parents[2] / "docs" / "data" / "reading_phrases.json"
    words = json.loads(data_file.read_text(encoding="utf-8"))["probe_words"]
    assert len(words) == 20
    missing = {w: p for w in words for p in word_phones(w) if p not in vocab}
    assert not missing
