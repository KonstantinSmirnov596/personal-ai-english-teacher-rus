"""Turns acoustic evidence into levels, scores and tips (plan items 3.4, 3.6, 3.7)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trainer.pron import catalog
from trainer.pron.catalog import CatalogEntry
from trainer.pron.engine import Evidence, PhoneEvidence
from trainer.pron.reference import Reference, RefPhone, Variant

LEVELS = ("ok", "approx", "error")
SEVERITY = {"ok": 0, "approx": 1, "error": 2}
MIN_SPEECH_RATIO = 0.3


@dataclass(frozen=True)
class Thresholds:
    """Log-likelihood-ratio margins (nats) a competitor must win by. Calibrated on synthetic
    speech (docs/reports/stage3.md); the owner's «Это было верно» shifts them per sound."""

    cat_error: float = 2.0  # catalogued mistake with default level «ошибка»
    cat_approx: float = 1.25  # ... shown as «неточно» below the error margin
    cat_soft: float = 1.75  # catalogued mistake whose default level is «неточно»
    gen_error: float = 7.0  # uncatalogued substitution
    gen_approx: float = 3.5
    del_approx: float = 3.0  # missing sound: never more than «неточно»
    strict_margin: float = 2.0  # extra margin for marks in free conversation


DEFAULT_THRESHOLDS = Thresholds()


class NoSpeechError(Exception):
    """Too little speech was recognized to compare with the reference."""


@dataclass
class Judgement:
    level: str
    heard: Variant | None  # None: said as expected
    entry: CatalogEntry | None
    llr: float
    gop: float


def judge(
    p: RefPhone,
    ev: PhoneEvidence,
    th: Thresholds = DEFAULT_THRESHOLDS,
    offset: float = 0.0,
    strict: bool = False,
) -> Judgement:
    if not p.scored or not ev.llr:
        return Judgement("ok", None, None, float("-inf"), 0.0)
    extra = offset + (th.strict_margin if strict else 0.0)
    best: tuple[int, float, Variant, CatalogEntry | None] | None = None
    top = max(ev.llr.values())
    for variant, llr in ev.llr.items():
        entry = catalog.match(p, variant)
        level = "ok"
        if entry is not None:
            if entry.default_level == "error":
                if llr >= th.cat_error + extra:
                    level = "error"
                elif llr >= th.cat_approx + extra and not strict:
                    level = "approx"
            elif llr >= th.cat_soft + extra and not strict:
                level = "approx"
        elif strict:
            level = "ok"
        elif variant == ():
            if llr >= th.del_approx + extra and not p.deletion_ok:
                level = "approx"
        elif llr >= th.gen_error + extra:
            level = "error"
        elif llr >= th.gen_approx + extra:
            level = "approx"
        cand = (SEVERITY[level], llr, variant, entry)
        if best is None or cand[:2] > best[:2]:
            best = cand
    assert best is not None
    severity, llr, variant, entry = best
    gop = -max(0.0, top)
    if severity == 0:
        return Judgement("ok", None, None, top, gop)
    return Judgement(LEVELS[severity], variant, entry, llr, gop)


def phone_score(gop: float, th: Thresholds = DEFAULT_THRESHOLDS) -> float:
    return float(min(1.0, max(0.0, 1.0 + gop / (2.0 * th.cat_error))))


def heard_symbol(variant: Variant | None, expected: str) -> str:
    if variant is None:
        return expected
    return "".join(variant)  # '' means the sound was not pronounced


def build_result(
    ref: Reference,
    evidence: Evidence,
    source_words: list[str],
    *,
    th: Thresholds = DEFAULT_THRESHOLDS,
    offsets: dict[str, float] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Assessment in the shape of docs/protocol.md («Разбор попытки»), without attempt_id."""
    if evidence.speech_ratio < MIN_SPEECH_RATIO:
        raise NoSpeechError
    offsets = offsets or {}
    judgements = [
        judge(p, e, th, offsets.get(p.cls, 0.0), strict)
        for p, e in zip(ref.phones, evidence.phones, strict=True)
    ]
    by_source: dict[int, list[int]] = {}
    for wi, w in enumerate(ref.words):
        by_source.setdefault(w.source_index, []).append(wi)
    words_out: list[dict[str, Any]] = []
    for source_index in sorted(by_source):
        phone_ids = [pi for wi in by_source[source_index] for pi in ref.words[wi].phones]
        phones_out: list[dict[str, Any]] = []
        worst: tuple[int, float, int] | None = None
        scores: list[float] = []
        for pi in phone_ids:
            p, e, j = ref.phones[pi], evidence.phones[pi], judgements[pi]
            phones_out.append(
                {
                    "expected": p.symbol,
                    "cls": p.cls,
                    "heard": heard_symbol(j.heard, p.symbol),
                    "start": e.start,
                    "end": e.end,
                    "gop": round(j.gop, 2),
                    "level": j.level,
                    "scored": p.scored,
                    "catalog_id": j.entry.id if j.entry else None,
                }
            )
            if p.scored:
                scores.append(phone_score(j.gop, th))
            key = (SEVERITY[j.level], j.llr, len(phones_out) - 1)
            if j.level != "ok" and (worst is None or key[:2] > worst[:2]):
                worst = key
        tip: dict[str, Any] | None = None
        if worst is not None:
            local = worst[2]
            j = judgements[phone_ids[local]]
            if j.entry is not None:
                tip = catalog.tip(j.entry, heard_symbol(j.heard, ""))
            else:
                tip = {"catalog_id": None, "title_ru": "", "tip_ru": "", "pair": None}
            tip["phone_index"] = local
            tip["expected"] = phones_out[local]["expected"]
            tip["heard"] = phones_out[local]["heard"]
        raw = source_words[source_index] if source_index < len(source_words) else ""
        words_out.append(
            {
                "w": raw.strip('.,!?;:"()[]…—–'),
                "source_index": source_index,
                "ipa": "".join(ref.phones[pi].symbol for pi in phone_ids),
                "score": round(sum(scores) / len(scores), 3) if scores else 1.0,
                "level": LEVELS[worst[0]] if worst else "ok",
                "phones": phones_out,
                "tip": tip,
            }
        )
    total = [w["score"] for w in words_out]
    return {
        "score": round(sum(total) / len(total), 3) if total else 0.0,
        "words": words_out,
        "duration_s": round(evidence.duration_s, 2),
    }
