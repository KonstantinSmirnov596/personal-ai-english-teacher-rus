"""Reference phonemes from espeak-ng (voice en-us), split per word.

The library comes from the ``espeakng-loader`` wheel, so no system-wide espeak-ng is needed.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from typing import Any

# Re-entrant: word_phones() holds the lock while _get_backend() takes it again.
_lock = threading.RLock()
_backend: Any = None

STRESS_MARKS = ("ˈ", "ˌ")


def _get_backend() -> Any:
    global _backend
    with _lock:
        if _backend is None:
            import espeakng_loader
            from phonemizer.backend import EspeakBackend
            from phonemizer.backend.espeak.wrapper import EspeakWrapper

            EspeakWrapper.set_library(espeakng_loader.get_library_path())
            EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
            _backend = EspeakBackend(
                "en-us",
                with_stress=False,
                preserve_punctuation=False,
                language_switch="remove-flags",
            )
        return _backend


def espeak_version() -> str:
    return ".".join(str(x) for x in _get_backend().version())


@lru_cache(maxsize=4096)
def word_phones(word: str) -> tuple[str, ...]:
    """Phones of a single word as espeak-ng en-us prints them (stress marks removed)."""
    from phonemizer.separator import Separator

    with _lock:
        out = _get_backend().phonemize(
            [word], separator=Separator(phone=" ", word=" | ", syllable=""), strip=True
        )[0]
    phones: list[str] = []
    for tok in out.replace("|", " ").split():
        for mark in STRESS_MARKS:
            tok = tok.replace(mark, "")
        if tok:
            phones.append(tok)
    return tuple(phones)
