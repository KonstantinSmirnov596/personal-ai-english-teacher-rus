"""Catalog of typical pronunciation mistakes of Russian speakers (trainer/data/l1_ru_catalog.json).

The catalog is written in ordinary IPA; here it is normalized to the scoring classes
(stage 0 report, «Фонемы») and matched against "expected -> heard" pairs (plan item 3.7).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from trainer.data_files import l1_catalog
from trainer.pron.inventory import R_COLORED
from trainer.pron.reference import RefPhone, Variant

# ordinary IPA of the catalog -> scoring class
IPA_TO_CLASS = {
    "ɝ": "ɜː",
    "ɜ": "ɜː",
    "ɚ": "ɜː",
    "ɑ": "ɑː",
    "ɔ": "ɔː",
    "o": "ɔː",
    "i": "iː",
    "u": "uː",
    "e": "ɛ",
    "a": "ɑː",
}


def to_class(symbol: str) -> str:
    return IPA_TO_CLASS.get(symbol, symbol)


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    position: str
    detect: str
    default_level: str
    priority: int
    title_ru: str
    tip_ru: str
    pairs: tuple[tuple[str, ...], ...]  # (expected class, heard key)
    examples: tuple[tuple[str, str, str], ...]  # (heard key, expected word, heard word)

    def example_for(self, heard: str) -> tuple[str, str] | None:
        for key, a, b in self.examples:
            if key == heard:
                return a, b
        return (self.examples[0][1], self.examples[0][2]) if self.examples else None


def heard_key(variant: Variant) -> str:
    """Key of a competitor: '' for a deletion, the class for one phone, joined for sequences."""
    return "".join(variant)


def _heard_to_key(heard: str) -> str:
    if len(heard) == 2 and heard[0] == "ŋ":  # catalog sequences "ŋk", "ŋɡ"
        return "ŋ" + to_class(heard[1])
    return to_class(heard)


@lru_cache(maxsize=1)
def entries() -> tuple[CatalogEntry, ...]:
    out: list[CatalogEntry] = []
    for e in l1_catalog()["entries"]:
        if e.get("detect") == "manual":
            continue
        pairs: list[tuple[str, ...]] = []
        if "expected_map" in e:
            pairs = [(to_class(a), to_class(b)) for a, b in e["expected_map"]]
        else:
            exp = to_class(e["expected"])
            if e.get("detect") == "deletion":
                pairs = [(exp, "")]
            for h in e.get("heard", []):
                pairs.append((exp, _heard_to_key(h)))
        examples = tuple(
            (_heard_to_key(p["heard"]), p["words"][0], p["words"][1]) for p in e.get("pairs", [])
        )
        out.append(
            CatalogEntry(
                id=e["id"],
                position=e.get("position", "any"),
                detect=e.get("detect", "substitution"),
                default_level=e.get("default_level", "approx"),
                priority=int(e.get("priority", 3)),
                title_ru=e.get("title_ru", ""),
                tip_ru=e.get("tip_ru", ""),
                pairs=tuple(pairs),
                examples=examples,
            )
        )
    return tuple(out)


def entry_by_id(entry_id: str) -> CatalogEntry | None:
    return next((e for e in entries() if e.id == entry_id), None)


def _position_ok(position: str, p: RefPhone) -> bool:
    if position == "any":
        return True
    if position == "word_final":
        return p.word_final
    if position == "stressed":
        return p.stressed
    if position == "unstressed":
        return not p.stressed
    if position == "after_vowel":
        return p.after_vowel or p.cls in R_COLORED or p.cls == "ɜː"
    return False


def _candidates(p: RefPhone, variant: Variant) -> list[tuple[str, str]]:
    """(expected, heard) keys under which a competitor is looked up in the catalog."""
    cls = p.cls
    key = heard_key(variant)
    out = [(cls, key)]
    if cls in R_COLORED:
        vowel = R_COLORED[cls]
        if variant == (vowel,):
            out.append(("ɹ", ""))  # r dropped after a vowel
        elif len(variant) == 2 and variant[0] == vowel and variant[1] in ("r", "ɾ"):
            out.append(("ɹ", variant[1]))  # trilled or tapped r
    elif cls == "ɜː":
        if variant == ("ə",) and not p.stressed:
            out.append(("ɹ", ""))  # "mother" without the r-coloring
        head = variant[0] if variant else ""
        if head in R_COLORED:  # "word" said as "ward"
            out.append((cls, R_COLORED[head]))
        elif len(variant) == 2 and variant[1] == "ɹ":
            out.append((cls, head))
    return out


def match(p: RefPhone, variant: Variant) -> CatalogEntry | None:
    """Catalog entry for «expected p, heard variant», or None for an uncatalogued confusion."""
    best: CatalogEntry | None = None
    for exp, heard in _candidates(p, variant):
        for e in entries():
            if (
                (exp, heard) in e.pairs
                and _position_ok(e.position, p)
                and (best is None or e.priority < best.priority)
            ):
                best = e
    return best


def tip(entry: CatalogEntry, heard: str) -> dict[str, Any]:
    pair = entry.example_for(heard)
    return {
        "catalog_id": entry.id,
        "title_ru": entry.title_ru,
        "tip_ru": entry.tip_ru,
        "pair": list(pair) if pair else None,
    }
