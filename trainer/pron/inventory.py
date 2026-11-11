"""Phone classes used for pronunciation scoring.

The acoustic model (wav2vec2-lv-60-espeak-cv-ft) has a multilingual vocabulary of 392 espeak
symbols. For English scoring we reduce its output to classes: one class per American English
phone as espeak-ng ``en-us`` prints it, plus a few accent sounds typical for Russian speakers
(trilled r, velar fricative). Symbols that differ only in length or are foreign spellings of the
same sound are merged into one class (stage 0 report, «Фонемы»).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# class name (canonical espeak symbol) -> member symbols of the model vocabulary
CONSONANTS: dict[str, tuple[str, ...]] = {
    "p": ("p", "ph", "pʰ"),
    "b": ("b",),
    "t": ("t", "th", "tʰ", "t̪"),
    "d": ("d",),
    "k": ("k", "kh", "kʰ"),
    "ɡ": ("ɡ",),
    "f": ("f",),
    "v": ("v",),
    "θ": ("θ",),
    "ð": ("ð",),
    "s": ("s", "s̪"),
    "z": ("z",),
    "ʃ": ("ʃ",),
    "ʒ": ("ʒ",),
    "h": ("h",),
    "m": ("m",),
    "n": ("n",),
    "ŋ": ("ŋ",),
    "l": ("l", "ɫ"),
    "ɹ": ("ɹ", "ɻ"),
    "w": ("w",),
    "j": ("j",),
    "tʃ": ("tʃ", "tS"),
    "dʒ": ("dʒ", "dZ"),
    "ɾ": ("ɾ",),
    "ʔ": ("ʔ",),
    # accent sounds
    "r": ("r",),
    "x": ("x", "χ"),
}

VOWELS: dict[str, tuple[str, ...]] = {
    "iː": ("iː", "i"),
    "ɪ": ("ɪ",),
    "ɛ": ("ɛ", "e"),
    "æ": ("æ",),
    "ɑː": ("ɑː", "ɑ", "a", "aː"),
    "ɔː": ("ɔː", "ɔ", "o", "oː"),
    "ʊ": ("ʊ",),
    "uː": ("uː", "u"),
    "ʌ": ("ʌ",),
    "ə": ("ə", "ɐ", "ᵻ"),
    "ɜː": ("ɜː", "ɚ", "ɜ"),
    "eɪ": ("eɪ",),
    "aɪ": ("aɪ",),
    "aʊ": ("aʊ",),
    "ɔɪ": ("ɔɪ", "oɪ"),
    "oʊ": ("oʊ", "əʊ"),
    # r-colored vowels printed by espeak as one symbol
    "ɑːɹ": ("ɑːɹ",),
    "ɔːɹ": ("ɔːɹ", "oːɹ"),
    "ɛɹ": ("ɛɹ",),
    "ɪɹ": ("ɪɹ",),
    "ʊɹ": ("ʊɹ",),
    "aɪɚ": ("aɪɚ", "aɪə"),
    # syllabic consonants
    "əl": ("əl",),
    "n̩": ("n̩",),
}

# r-colored class -> (vowel class, "ɹ"): both spellings are the same pronunciation
R_COLORED: dict[str, str] = {
    "ɑːɹ": "ɑː",
    "ɔːɹ": "ɔː",
    "ɛɹ": "ɛ",
    "ɪɹ": "ɪ",
    "ʊɹ": "ʊ",
}
SYLLABIC = frozenset({"əl", "n̩"})
PLAIN_VOWELS = tuple(v for v in VOWELS if v not in R_COLORED and v not in SYLLABIC and v != "aɪɚ")

BLANK = 0  # class index of the CTC blank in the reduced matrix


@dataclass(frozen=True)
class Inventory:
    names: tuple[str, ...]  # index 0 is the blank
    index: dict[str, int]
    symbol_to_class: dict[str, str]
    membership: np.ndarray  # (model vocab, classes) 0/1 matrix, float32

    def cls(self, name: str) -> int:
        return self.index[name]

    def class_of_symbol(self, symbol: str) -> str | None:
        return self.symbol_to_class.get(symbol)

    def is_vowel(self, name: str) -> bool:
        return name in VOWELS


def build_inventory(vocab: dict[str, int], blank_id: int) -> Inventory:
    names = ["<blank>", *CONSONANTS, *VOWELS]
    index = {n: i for i, n in enumerate(names)}
    symbol_to_class: dict[str, str] = {}
    membership = np.zeros((len(vocab), len(names)), dtype=np.float32)
    membership[blank_id, BLANK] = 1.0
    for table in (CONSONANTS, VOWELS):
        for name, members in table.items():
            for sym in members:
                if sym in vocab:
                    membership[vocab[sym], index[name]] = 1.0
                    symbol_to_class[sym] = name
    return Inventory(
        names=tuple(names),
        index=index,
        symbol_to_class=symbol_to_class,
        membership=membership,
    )
