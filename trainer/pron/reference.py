"""Reference pronunciation: text -> espeak-ng en-us phones with word boundaries (plan item 3.1).

The phrase is phonemized as a whole, so weak forms and flaps come out the way an American says
them in running speech — and the way the acoustic model was trained to label them. espeak may
merge neighbouring function words ("on the" -> one unit) or expand one token into several
(numbers); units are mapped back to words with a small dynamic program over per-word phones.

Each reference position carries the pronunciations accepted as correct (American tolerances
from docs/design.md and the catalog) and the competitors that are scored against it.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from trainer.pron.inventory import CONSONANTS, PLAIN_VOWELS, R_COLORED, SYLLABIC, VOWELS

Variant = tuple[str, ...]  # sequence of class names; () is a deletion

STRESS = ("ˈ", "ˌ")
TOKEN = re.compile(r"[A-Za-z0-9']+")

# Words whose weak and full forms are both correct (catalog tolerance «weak_forms», extended
# to the other English function words that reduce in running speech).
FUNCTION_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "to",
        "of",
        "for",
        "and",
        "can",
        "at",
        "as",
        "or",
        "but",
        "from",
        "was",
        "were",
        "are",
        "is",
        "am",
        "be",
        "been",
        "have",
        "has",
        "had",
        "do",
        "does",
        "will",
        "would",
        "should",
        "could",
        "than",
        "that",
        "her",
        "him",
        "them",
        "us",
        "you",
        "your",
        "some",
        "in",
        "on",
        "it",
        "its",
        "i",
        "he",
        "she",
        "we",
        "they",
        "my",
        "me",
        "his",
        "our",
        "their",
        "if",
        "so",
        "not",
    }
)

_lock = threading.RLock()
_backend: Any = None


def _stress_backend() -> Any:
    global _backend
    with _lock:
        if _backend is None:
            import espeakng_loader
            from phonemizer.backend import EspeakBackend
            from phonemizer.backend.espeak.wrapper import EspeakWrapper

            from trainer import espeak_path

            espeak_path.configure()  # non-ASCII app folders (see trainer/espeak_path.py)
            EspeakWrapper.set_library(espeakng_loader.get_library_path())
            EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
            _backend = EspeakBackend(
                "en-us",
                with_stress=True,
                preserve_punctuation=False,
                language_switch="remove-flags",
                words_mismatch="ignore",
            )
        return _backend


def _phonemize(text: str) -> list[list[str]]:
    """Units of stress-marked espeak symbols for a text ("unit" is usually one word)."""
    from phonemizer.separator import Separator

    with _lock:
        out = _stress_backend().phonemize(
            [text], separator=Separator(phone=" ", word=" | ", syllable=""), strip=True
        )[0]
    return [u.split() for u in out.split("|") if u.split()]


@lru_cache(maxsize=8192)
def _word_alone(word: str) -> tuple[str, ...]:
    return tuple(tok for unit in _phonemize(word) for tok in unit)


def _plain(token: str) -> str:
    for mark in STRESS:
        token = token.replace(mark, "")
    return token


def _edit_distance(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, start=1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return prev[-1]


def _split_unit(unit: list[str], parts: list[list[str]]) -> list[list[str]]:
    """Split one merged unit into len(parts) words, guided by the words' isolated phones."""
    if len(parts) == 1:
        return [unit]
    n = len(unit)
    best: tuple[int, list[int]] | None = None

    def search(k: int, start: int, cost: int, cuts: list[int]) -> None:
        nonlocal best
        if best is not None and cost >= best[0]:
            return
        if k == len(parts) - 1:
            total = cost + _edit_distance([_plain(t) for t in unit[start:]], parts[k])
            if best is None or total < best[0]:
                best = (total, [*cuts, n])
            return
        for cut in range(start + 1, n - (len(parts) - k - 1) + 1):
            c = _edit_distance([_plain(t) for t in unit[start:cut]], parts[k])
            search(k + 1, cut, cost + c, [*cuts, cut])

    if n < len(parts):
        return [unit] + [[] for _ in parts[1:]]
    search(0, 0, 0, [])
    assert best is not None
    out: list[list[str]] = []
    start = 0
    for cut in best[1]:
        out.append(unit[start:cut])
        start = cut
    return out


def _units_to_words(units: list[list[str]], words: list[str]) -> list[list[str]]:
    """Map espeak units onto words: a unit may cover several words and vice versa."""
    if len(units) == len(words):
        return units
    alone = [[_plain(t) for t in _word_alone(w)] for w in words]
    m, n = len(units), len(words)
    inf = 10**9
    max_span = 4
    cost = [[inf] * (n + 1) for _ in range(m + 1)]
    back: list[list[tuple[int, int] | None]] = [[None] * (n + 1) for _ in range(m + 1)]
    cost[0][0] = 0
    for i in range(m + 1):
        for j in range(n + 1):
            if cost[i][j] >= inf:
                continue
            if i < m:  # one unit covers k words
                for k in range(1, max_span + 1):
                    if j + k > n:
                        break
                    target = [p for w in alone[j : j + k] for p in w]
                    c = cost[i][j] + _edit_distance([_plain(t) for t in units[i]], target)
                    if c < cost[i + 1][j + k]:
                        cost[i + 1][j + k] = c
                        back[i + 1][j + k] = (i, j)
            if j < n:  # one word covers k units (numbers, abbreviations)
                for k in range(2, max_span + 1):
                    if i + k > m:
                        break
                    source = [_plain(t) for u in units[i : i + k] for t in u]
                    c = cost[i][j] + _edit_distance(source, alone[j])
                    if c < cost[i + k][j + 1]:
                        cost[i + k][j + 1] = c
                        back[i + k][j + 1] = (i, j)
    if cost[m][n] >= inf:
        return [list(_word_alone(w)) for w in words]  # isolated forms as a last resort
    out: list[list[str]] = [[] for _ in words]
    i, j = m, n
    while (i, j) != (0, 0):
        prev = back[i][j]
        assert prev is not None
        pi, pj = prev
        if i - pi == 1:
            pieces = _split_unit(units[pi], alone[pj:j])
            for offset, piece in enumerate(pieces):
                out[pj + offset] = piece
        else:
            out[pj] = [t for u in units[pi:i] for t in u]
        i, j = pi, pj
    return out


@dataclass
class RefPhone:
    symbol: str  # espeak symbol shown to the learner
    cls: str  # class name used for statistics and the catalog
    word: int
    index_in_word: int
    stressed: bool
    is_vowel: bool
    word_initial: bool = False
    word_final: bool = False
    after_vowel: bool = False
    function_word: bool = False
    canonical: Variant = ()
    accept: list[Variant] = field(default_factory=list)
    competitors: list[Variant] = field(default_factory=list)
    scored: bool = True
    deletion_ok: bool = False


@dataclass
class RefWord:
    text: str
    source_index: int
    phones: list[int] = field(default_factory=list)  # indices into Reference.phones

    @property
    def is_function(self) -> bool:
        return self.text.lower().strip("'") in FUNCTION_WORDS


@dataclass
class Reference:
    words: list[RefWord]
    phones: list[RefPhone]

    def ipa(self, word_index: int) -> str:
        return "".join(self.phones[i].symbol for i in self.words[word_index].phones)


def tokenize(source_words: list[str]) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for idx, raw in enumerate(source_words):
        for tok in TOKEN.findall(raw.replace("’", "'").replace("-", " ").replace("—", " ")):
            if tok.strip("'"):
                out.append((tok, idx))
    return out


def _class_of(symbol: str) -> str | None:
    for table in (CONSONANTS, VOWELS):
        for name, members in table.items():
            if symbol in members:
                return name
    return None


def build_reference(source_words: list[str]) -> Reference:
    """Build the reference for a list of source words (display words or recognized words)."""
    tokens = tokenize(source_words)
    words = [RefWord(text=t, source_index=i) for t, i in tokens]
    if not words:
        return Reference(words=[], phones=[])
    units = _phonemize(" ".join(w.text for w in words))
    per_word = _units_to_words(units, [w.text for w in words])

    phones: list[RefPhone] = []
    for wi, (word, toks) in enumerate(zip(words, per_word, strict=True)):
        entries: list[tuple[str, str, bool]] = []  # (symbol, class, stressed)
        for tok in toks:
            sym = _plain(tok)
            cls = _class_of(sym) or _class_of(sym.replace("ː", ""))
            if cls is None:
                continue  # symbol outside the scoring inventory: not scored, not aligned
            entries.append((sym, cls, tok != sym))
        merged: list[tuple[str, str, bool, Variant]] = []
        k = 0
        while k < len(entries):
            sym, cls, stressed = entries[k]
            nxt = entries[k + 1] if k + 1 < len(entries) else None
            after = entries[k + 2] if k + 2 < len(entries) else None
            combined = next((rc for rc, v in R_COLORED.items() if v == cls), None)
            coda_r = nxt is not None and nxt[1] == "ɹ" and (after is None or after[1] not in VOWELS)
            if combined is not None and coda_r:
                assert nxt is not None
                merged.append((sym + nxt[0], combined, stressed, (cls, "ɹ")))
                k += 2
                continue
            merged.append((sym, cls, stressed, (cls,)))
            k += 1
        for pi, (sym, cls, stressed, canonical) in enumerate(merged):
            word.phones.append(len(phones))
            phones.append(
                RefPhone(
                    symbol=sym,
                    cls=cls,
                    word=wi,
                    index_in_word=pi,
                    stressed=stressed,
                    is_vowel=cls in VOWELS,
                    word_initial=pi == 0,
                    word_final=pi == len(merged) - 1,
                    function_word=word.is_function,
                    canonical=canonical,
                )
            )
    ref = Reference(words=words, phones=phones)
    _fill_variants(ref)
    return ref


def _fill_variants(ref: Reference) -> None:
    phones = ref.phones
    for i, p in enumerate(phones):
        prev = phones[i - 1] if i > 0 else None
        nxt = phones[i + 1] if i + 1 < len(phones) else None
        same_word_prev = prev is not None and prev.word == p.word
        same_word_next = nxt is not None and nxt.word == p.word
        p.after_vowel = bool(same_word_prev and prev is not None and prev.is_vowel)
        accept: list[Variant] = [p.canonical]

        def add(*variants: Variant, accept: list[Variant] = accept) -> None:
            for v in variants:
                if v not in accept:
                    accept.append(v)

        cls = p.cls
        if cls in R_COLORED:
            add((cls,), (R_COLORED[cls], "ɹ"))
        elif cls == "aɪɚ":
            add(("aɪɚ",), ("aɪ", "ɜː"))
        elif cls in SYLLABIC:
            base = "l" if cls == "əl" else "n"
            add((cls,), ("ə", base), (base,))
            p.scored = False
        elif cls == "ɾ":
            add(("t",), ("d",))
        elif cls in ("t", "d"):
            intervocalic = (
                p.after_vowel
                and same_word_next
                and nxt is not None
                and nxt.is_vowel
                and not nxt.stressed
            )
            # linking: a word-final /t, d/ before a vowel is flapped in running speech
            linking = p.word_final and p.after_vowel and nxt is not None and nxt.is_vowel
            if intervocalic or linking:
                add(("ɾ",))
            if cls == "t" and p.word_final:
                add(("ʔ",), ())  # unreleased or glottal final /t/
        elif cls == "ɑː":
            add(("ɔː",))  # cot–caught merger
        elif cls == "ɔː":
            add(("ɑː",))
        elif cls == "ʌ":
            add(("ə",))
            if same_word_next and nxt is not None and nxt.cls == "ɹ":
                add(("ɜː",))  # hurry–furry merger: "worry" with the NURSE vowel
        elif cls == "ɜː":
            if nxt is not None and nxt.cls == "ɹ":
                add(("ə",))  # "around": espeak prints ɚ before a linking /ɹ/
        elif cls == "ɹ":
            if same_word_prev and prev is not None and prev.cls == "ʌ":
                add(())  # absorbed into the NURSE vowel (see above)
        elif cls == "ə":
            add(("ʌ",), ("ɪ",))
            if not p.stressed:
                add(("iː",))
        elif cls in ("ɪ", "iː") and not p.stressed:
            add(("ə",), ("ɪ",), ("iː",))

        # the same sound on both sides of a word boundary is said once ("this seat")
        for other in (prev, nxt):
            if other is not None and other.word != p.word and other.cls == cls and not p.is_vowel:
                p.deletion_ok = True
        if p.function_word:
            p.deletion_ok = True
            if p.is_vowel:
                p.scored = False
        p.accept = accept

        if not p.scored:
            p.competitors = []
            continue
        flat = {v[0] for v in accept if len(v) == 1}
        competitors: list[Variant] = []
        if cls in R_COLORED:
            vowel = R_COLORED[cls]
            competitors = [(vowel,), (vowel, "r"), (vowel, "ɾ"), ("ɜː",)]
            competitors += [(rc,) for rc in R_COLORED if rc != cls]
        elif p.is_vowel:
            competitors = [(v,) for v in PLAIN_VOWELS if v not in flat]
            if cls == "ɜː":  # "word" said as "ward": an r-colored vowel in its place
                competitors += [(rc,) for rc in R_COLORED]
                competitors += [(v, "ɹ") for v in R_COLORED.values()]
            competitors.append(())
        else:
            competitors = [(c,) for c in CONSONANTS if c not in flat and c != "ʔ"]
            if cls == "ŋ" and p.word_final:
                competitors += [("ŋ", "k"), ("ŋ", "ɡ")]
            if () not in accept:
                competitors.append(())
        p.competitors = competitors
