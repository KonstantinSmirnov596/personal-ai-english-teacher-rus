"""Stage 0.7: compare espeak-ng en-us symbols with the wav2vec2 phoneme vocabulary.

For the 20 probe words of docs/data/reading_phrases.json:
1. espeak-ng en-us phones and whether every symbol exists in the model vocabulary;
2. free phoneme recognition of each word read by three Kokoro voices, aligned to the espeak
   reference, to list the symbols the model actually prints instead.

Writes docs/reports/data/phonemes_stage0.json and prints a summary.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from trainer.paths import configure_model_env  # noqa: E402

configure_model_env()

VOICES = ("af_heart", "am_michael", "af_bella")


def align(ref: list[str], hyp: list[str]) -> list[tuple[str | None, str | None]]:
    """Levenshtein alignment of two symbol lists."""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(
                d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1])
            )
    out: list[tuple[str | None, str | None]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]):
            out.append((ref[i - 1], hyp[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            out.append((ref[i - 1], None))
            i -= 1
        else:
            out.append((None, hyp[j - 1]))
            j -= 1
    return out[::-1]


def main() -> int:
    from trainer.gpu_env import prepare_cuda_dlls

    prepare_cuda_dlls()
    from trainer.pron.espeak import espeak_version, word_phones
    from trainer.pron.phoneme_model import PhonemeModel
    from trainer.speech.audio import STT_RATE, resample
    from trainer.speech.tts import SpeechSynthesizer

    data = json.loads((REPO / "docs" / "data" / "reading_phrases.json").read_text(encoding="utf-8"))
    words: list[str] = data["probe_words"]

    model = PhonemeModel()
    model.load()
    vocab = set(model.vocab)
    tts = SpeechSynthesizer()

    rows: list[dict[str, Any]] = []
    oov: Counter[str] = Counter()
    subs: Counter[tuple[str, str]] = Counter()
    dels: Counter[str] = Counter()
    ins: Counter[str] = Counter()
    exact = 0
    total = 0
    for word in words:
        ref = list(word_phones(word))
        missing = [p for p in ref if p not in vocab]
        oov.update(missing)
        heard: dict[str, list[str]] = {}
        for voice in VOICES:
            syn = tts.synthesize(word, voice=voice)
            audio = resample(syn.audio, syn.sample_rate, STT_RATE)
            hyp = model.greedy_decode(model.log_probs(audio))
            heard[voice] = hyp
            total += 1
            exact += hyp == ref
            for r, h in align(ref, hyp):
                if r is not None and h is not None and r != h:
                    subs[(r, h)] += 1
                elif r is not None and h is None:
                    dels[r] += 1
                elif r is None and h is not None:
                    ins[h] += 1
        rows.append({"word": word, "espeak": ref, "not_in_vocab": missing, "heard": heard})

    result = {
        "espeak_version": espeak_version(),
        "vocab_size": len(vocab),
        "words": rows,
        "symbols_not_in_vocab": dict(oov),
        "exact_match": {"matched": exact, "total": total},
        "substitutions": [
            {"expected": r, "heard": h, "count": c} for (r, h), c in subs.most_common()
        ],
        "deletions": dict(dels.most_common()),
        "insertions": dict(ins.most_common()),
    }
    out = REPO / "docs" / "reports" / "data" / "phonemes_stage0.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"espeak-ng {result['espeak_version']}, vocab {len(vocab)} symbols")
    for r in rows:
        heard = "; ".join(f"{v}: {' '.join(h)}" for v, h in r["heard"].items())
        print(f"{r['word']:8} espeak: {' '.join(r['espeak']):14} | {heard}")
    print("not in vocab:", dict(oov) or "none")
    print(f"exact match: {exact}/{total}")
    print("substitutions:", [(r, h, c) for (r, h), c in subs.most_common(15)])
    print("deletions:", dict(dels.most_common(10)), "insertions:", dict(ins.most_common(10)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
