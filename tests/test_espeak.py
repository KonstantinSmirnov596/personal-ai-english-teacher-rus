"""espeak-ng is available without a system install (bundled by espeakng-loader)."""

from __future__ import annotations

import espeakng_loader
from phonemizer.backend import EspeakBackend
from phonemizer.backend.espeak.wrapper import EspeakWrapper
from phonemizer.separator import Separator


def test_espeak_en_us_phonemizes() -> None:
    EspeakWrapper.set_library(espeakng_loader.get_library_path())
    EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
    backend = EspeakBackend("en-us", with_stress=False)
    out = backend.phonemize(["think"], separator=Separator(phone=" ", word="|"), strip=True)
    assert out[0].split() == ["θ", "ɪ", "ŋ", "k"]
