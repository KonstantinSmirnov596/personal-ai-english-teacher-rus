"""Stage 3 evaluation of pronunciation scoring on synthesized speech.

1. False alarms: three American Kokoro voices read every reading phrase; share of phones that
   get the level «ошибка» (and «неточно»), overall and per sound.
2. Substitutions: for the minimal pairs of the catalog the second word is spoken where the
   first is expected; how often the target phone is flagged and the heard sound is named.

Writes docs/reports/data/pron_stage3.json and prints a summary.
    .venv\\Scripts\\python.exe scripts\\pron_eval.py [--carrier "Say {word} for me."]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

NAMED_PAIRS = (
    ("think", "sink"),
    ("west", "vest"),
    ("sing", "sin"),
    ("bad", "bat"),
    ("ship", "sheep"),
    ("then", "den"),
)


def target_position(
    ref: Any, ref_actual: Any, word_index: int
) -> tuple[int, list[tuple[str, ...]]]:
    """Reference phone where the expected and the actually spoken word differ.

    Returns the phone index and the variants that count as a correct diagnosis.
    """
    from trainer.pron.align import needleman_wunsch

    exp_ids = [pi for w in ref.words if w.source_index == word_index for pi in w.phones]
    act_ids = [pi for w in ref_actual.words if w.source_index == word_index for pi in w.phones]
    exp = ["+".join(ref.phones[i].canonical) for i in exp_ids]
    act = ["+".join(ref_actual.phones[i].canonical) for i in act_ids]
    pairs = needleman_wunsch(exp, act)
    last_exp: int | None = None
    for k, (ei, ai) in enumerate(pairs):
        if ei is not None and ai is not None and exp[ei] != act[ai]:
            actual = ref_actual.phones[act_ids[ai]]
            return exp_ids[ei], list(actual.accept)
        if ei is None and ai is not None:  # extra sound in the spoken word
            anchor = last_exp if last_exp is not None else 0
            base = ref.phones[exp_ids[anchor]].canonical
            inserted = ref_actual.phones[act_ids[ai]].canonical
            return exp_ids[anchor], [base + inserted]
        if ei is not None and ai is None:
            return exp_ids[ei], [()]
        if ei is not None:
            last_exp = ei
        _ = k
    return exp_ids[-1], []


def evaluate(carriers: list[str] | None = None) -> dict[str, Any]:
    from tests.fixtures.make_fixtures import ensure_pairs, ensure_reading
    from trainer.gpu_env import prepare_cuda_dlls

    prepare_cuda_dlls()
    from trainer.pron.assess import build_result, judge
    from trainer.pron.engine import PronEngine
    from trainer.pron.phoneme_model import PhonemeModel
    from trainer.pron.reference import build_reference
    from trainer.speech.audio import STT_RATE, read_wav, resample

    engine = PronEngine(PhonemeModel())

    def load(path: Path) -> Any:
        audio, rate = read_wav(path)
        return resample(audio, rate, STT_RATE)

    # --- false alarms on native-like reading ------------------------------------------------
    per_sound: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "error": 0, "approx": 0})
    total = errors = approx = 0
    offenders: list[dict[str, Any]] = []
    timings: list[tuple[float, float]] = []
    for item in ensure_reading():
        text = item["phrase"]["text"]
        source = text.split()
        audio = load(item["path"])
        started = time.perf_counter()
        ref = build_reference(source)
        evidence = engine.analyze(audio, ref)
        result = build_result(ref, evidence, source)
        timings.append((len(audio) / STT_RATE, time.perf_counter() - started))
        for word in result["words"]:
            for ph in word["phones"]:
                total += 1
                stat = per_sound[ph["cls"]]
                stat["n"] += 1
                if ph["level"] == "error":
                    errors += 1
                    stat["error"] += 1
                elif ph["level"] == "approx":
                    approx += 1
                    stat["approx"] += 1
                if ph["level"] != "ok":
                    offenders.append(
                        {
                            "phrase": item["phrase"]["id"],
                            "voice": item["voice"],
                            "word": word["w"],
                            "expected": ph["expected"],
                            "heard": ph["heard"],
                            "level": ph["level"],
                            "gop": ph["gop"],
                        }
                    )

    # --- substitutions ------------------------------------------------------------------------
    cases: list[dict[str, Any]] = []
    for item in ensure_pairs(carriers=carriers):
        source = item["reference_text"].split()
        ref = build_reference(source)
        ref_actual = build_reference(item["actual_text"].split())
        ti, correct = target_position(ref, ref_actual, item["word_index"])
        evidence = engine.analyze(load(item["path"]), ref)
        j = judge(ref.phones[ti], evidence.phones[ti])
        named = j.heard is not None and (
            j.heard in correct or "".join(j.heard) in {"".join(c) for c in correct}
        )
        cases.append(
            {
                "entry": item["entry_id"],
                "default_level": item["default_level"],
                "expected": item["expected"],
                "actual": item["actual"],
                "voice": item["voice"],
                "carrier": item["carrier"],
                "target": ref.phones[ti].symbol,
                "level": j.level,
                "heard": "".join(j.heard) if j.heard is not None else None,
                "llr": round(j.llr, 2),
                "diagnosis_ok": bool(named),
            }
        )

    def rates(selected: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(selected)
        return {
            "cases": n,
            "error": round(sum(c["level"] == "error" for c in selected) / n, 3) if n else None,
            "flagged": round(sum(c["level"] != "ok" for c in selected) / n, 3) if n else None,
            "diagnosis": round(sum(c["diagnosis_ok"] for c in selected) / n, 3) if n else None,
        }

    named_cases = [c for c in cases if (c["expected"], c["actual"]) in NAMED_PAIRS]
    error_cases = [c for c in cases if c["default_level"] == "error"]
    by_entry = {
        entry: rates([c for c in cases if c["entry"] == entry])
        for entry in dict.fromkeys(c["entry"] for c in cases)
    }
    by_pair = {
        f"{a}/{b}": rates([c for c in cases if (c["expected"], c["actual"]) == (a, b)])
        for a, b in dict.fromkeys((c["expected"], c["actual"]) for c in cases)
    }
    five = [t for d, t in timings if 4.0 <= d <= 6.0] or [t for _, t in timings]
    return {
        "carriers": sorted({c["carrier"] for c in cases}),
        "false_alarms": {
            "phones": total,
            "error": errors,
            "approx": approx,
            "error_rate": round(errors / total, 4),
            "approx_rate": round(approx / total, 4),
            "per_sound": dict(sorted(per_sound.items(), key=lambda kv: -kv[1]["n"])),
            "offenders": offenders,
        },
        "substitutions": {
            "named_pairs": rates(named_cases),
            "error_level_entries": rates(error_cases),
            "all": rates(cases),
            "by_entry": by_entry,
            "by_pair": by_pair,
            "cases": cases,
        },
        "timing": {
            "analysis_s_median_for_5s_phrase": round(sorted(five)[len(five) // 2], 3),
            "analysis_s_max": round(max(t for _, t in timings), 3),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--carrier", action="append", default=None, help="experimental carrier")
    ap.add_argument("--out", default=str(REPO / "docs" / "reports" / "data" / "pron_stage3.json"))
    args = ap.parse_args()
    result = evaluate(args.carrier)
    if args.carrier is None:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    fa = result["false_alarms"]
    print(
        f"false alarms: error {fa['error']}/{fa['phones']} = {fa['error_rate']:.2%},"
        f" approx {fa['approx_rate']:.2%}"
    )
    for o in fa["offenders"]:
        print(
            f"   {o['level']:6} {o['phrase']} {o['voice']:10} {o['word']:10}"
            f" {o['expected']} -> {o['heard'] or '∅'} ({o['gop']})"
        )
    sub = result["substitutions"]
    print("named pairs:", sub["named_pairs"])
    print("error-level entries:", sub["error_level_entries"])
    for entry, r in sub["by_entry"].items():
        print(f"   {entry:16} {r}")
    for c in sub["cases"]:
        mark = (
            "ok " if c["level"] == "error" else "MISS" if c["default_level"] == "error" else "    "
        )
        print(
            f"   {mark} {c['carrier'][:8]:8} {c['entry']:16} {c['expected']:7}->{c['actual']:7}"
            f" {c['voice']:10}"
            f" {c['target']:3} heard={c['heard'] or '-':4} {c['level']:6} llr={c['llr']}"
            f" diag={'+' if c['diagnosis_ok'] else '-'}"
        )
    print("timing:", result["timing"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
