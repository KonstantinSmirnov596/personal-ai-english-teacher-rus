"""Word error rate with simple normalization (lowercase, no punctuation, numbers as words)."""

from __future__ import annotations

import re

from num2words import num2words

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_NON_WORD = re.compile(r"[^a-z0-9' ]+")


def normalize(text: str) -> list[str]:
    lowered = text.lower().replace("-", " ").replace("’", "'")
    spelled = _NUMBER.sub(lambda m: " " + num2words(m.group(0)) + " ", lowered)
    cleaned = _NON_WORD.sub(" ", spelled.replace("-", " ").replace(",", " "))
    return [w.strip("'") for w in cleaned.split() if w.strip("'")]


def edit_distance(ref: list[str], hyp: list[str]) -> int:
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, start=1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1]


def word_errors(reference: str, hypothesis: str) -> tuple[int, int]:
    """(errors, reference word count) after normalization."""
    ref = normalize(reference)
    hyp = normalize(hypothesis)
    return edit_distance(ref, hyp), len(ref)


def wer(references: list[str], hypotheses: list[str]) -> float:
    """Corpus-level WER: total edits over total reference words."""
    errors = 0
    total = 0
    for ref, hyp in zip(references, hypotheses, strict=True):
        e, n = word_errors(ref, hyp)
        errors += e
        total += n
    return errors / total if total else 0.0
