from __future__ import annotations

from trainer.wer import normalize, wer


def test_normalize_numbers_and_punctuation() -> None:
    assert normalize("I have 2 cats, OK?") == ["i", "have", "two", "cats", "ok"]


def test_wer_counts_edits() -> None:
    assert wer(["a b c d"], ["a x c"]) == 0.5
    assert wer(["Hello, world!"], ["hello world"]) == 0.0
