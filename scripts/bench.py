"""Stage 0 benchmark: recognition and synthesis latency, VRAM per model, TTS -> STT loop.

Run in a fresh process (cold numbers depend on it):
    .venv\\Scripts\\python.exe scripts\\bench.py [--out docs/reports/data/bench_stage0.json]
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from trainer.paths import configure_model_env  # noqa: E402

configure_model_env()

import os  # noqa: E402

os.environ.setdefault("HF_HUB_OFFLINE", "1")

WARM_RUNS = 5


def device_used_mb() -> int:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return int(out.split(",")[0])


def device_total_mb() -> int:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return int(out)


def timed(fn: Any) -> tuple[float, Any]:
    t = time.perf_counter()
    res = fn()
    return (time.perf_counter() - t) * 1000.0, res


def stats(values: list[float]) -> dict[str, float]:
    return {
        "median_ms": round(statistics.median(values), 1),
        "max_ms": round(max(values), 1),
        "runs": len(values),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "docs" / "reports" / "data" / "bench_stage0.json"))
    args = ap.parse_args()

    from tests.fixtures.make_fixtures import ensure_loop, load_spec
    from trainer.gpu_env import prepare_cuda_dlls

    prepare_cuda_dlls()
    import torch

    result: dict[str, Any] = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "gpu": torch.cuda.get_device_name(0),
        "torch": torch.__version__,
        "vram_total_mb": device_total_mb(),
    }
    vram: dict[str, int] = {}
    base = device_used_mb()
    vram["baseline_before_process"] = base
    torch.zeros(1, device="cuda")
    torch.cuda.synchronize()
    vram["cuda_context"] = device_used_mb() - base

    spec = load_spec()["bench"]

    # --- TTS ---------------------------------------------------------------------------
    from trainer.speech.audio import STT_RATE, resample
    from trainer.speech.tts import SpeechSynthesizer

    tts = SpeechSynthesizer()
    before = device_used_mb()
    load_ms, _ = timed(tts.load)
    first_ms, first = timed(lambda: tts.synthesize(spec["tutor_first_sentence"], spec["voice"]))
    torch.cuda.synchronize()
    vram["tts"] = device_used_mb() - before
    warm = []
    for _ in range(WARM_RUNS):
        ms, _r = timed(lambda: tts.synthesize(spec["tutor_first_sentence"], spec["voice"]))
        warm.append(ms)
    result["tts"] = {
        "load_ms": round(load_ms, 1),
        "first_sentence_cold_ms": round(first_ms, 1),
        "first_sentence_warm": stats(warm),
        "sentence_audio_s": round(len(first.audio) / first.sample_rate, 2),
        "sentence": spec["tutor_first_sentence"],
    }

    phrases = {}
    for key in ("phrase_5s", "phrase_10s"):
        syn = tts.synthesize(spec[key], spec["voice"])
        phrases[key] = resample(syn.audio, syn.sample_rate, STT_RATE)

    # --- STT ---------------------------------------------------------------------------
    from trainer.speech.stt import SpeechRecognizer

    stt = SpeechRecognizer()
    before = device_used_mb()
    load_ms, _ = timed(stt.load)
    stt_res: dict[str, Any] = {"load_ms": round(load_ms, 1), "beam_size": stt.beam_size}
    for key, audio in phrases.items():
        cold_ms, tr = timed(lambda a=audio: stt.transcribe(a))
        warm = [timed(lambda a=audio: stt.transcribe(a))[0] for _ in range(WARM_RUNS)]
        stt_res[key] = {
            "audio_s": round(len(audio) / STT_RATE, 2),
            "cold_ms": round(cold_ms, 1),
            "warm": stats(warm),
            "text": tr.text,
        }
    torch.cuda.synchronize()
    vram["stt"] = device_used_mb() - before
    # Beam search 5 for comparison (default decoding of faster-whisper).
    stt5 = SpeechRecognizer(beam_size=5)
    stt5._model = stt._model  # same weights, different decoding
    warm5 = [timed(lambda: stt5.transcribe(phrases["phrase_10s"]))[0] for _ in range(WARM_RUNS)]
    stt_res["phrase_10s_beam5_warm"] = stats(warm5)
    result["stt"] = stt_res

    # --- Phoneme model -----------------------------------------------------------------
    from trainer.pron.phoneme_model import PhonemeModel

    ph = PhonemeModel()
    before = device_used_mb()
    load_ms, _ = timed(ph.load)
    cold_ms, _lp = timed(lambda: ph.log_probs(phrases["phrase_5s"]))
    warm = [timed(lambda: ph.log_probs(phrases["phrase_5s"]))[0] for _ in range(WARM_RUNS)]
    torch.cuda.synchronize()
    vram["phoneme"] = device_used_mb() - before
    result["phoneme"] = {
        "load_ms": round(load_ms, 1),
        "phrase_5s_cold_ms": round(cold_ms, 1),
        "phrase_5s_warm": stats(warm),
    }

    vram["all_loaded_device_used"] = device_used_mb()
    vram["all_loaded_process"] = vram["all_loaded_device_used"] - base
    vram["torch_max_allocated"] = int(torch.cuda.max_memory_allocated() // 2**20)
    result["vram_mb"] = vram

    # --- TTS -> STT loop ----------------------------------------------------------------
    from trainer.speech.audio import read_wav
    from trainer.wer import wer, word_errors

    items = ensure_loop()
    refs: list[str] = []
    hyps: list[str] = []
    per: list[dict[str, Any]] = []
    for path, text in items:
        audio, rate = read_wav(path)
        hyp = stt.transcribe(resample(audio, rate, STT_RATE)).text
        e, n = word_errors(text, hyp)
        per.append({"file": path.name, "ref": text, "hyp": hyp, "errors": e, "words": n})
        refs.append(text)
        hyps.append(hyp)
    result["loop"] = {"wer": round(wer(refs, hyps), 4), "phrases": len(items), "items": per}

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    s = result["stt"]
    print(f"GPU: {result['gpu']}, torch {result['torch']}")
    print(f"STT load {s['load_ms']} ms")
    for key in ("phrase_5s", "phrase_10s"):
        p = s[key]
        print(
            f"STT {key} ({p['audio_s']} s): cold {p['cold_ms']} ms, "
            f"warm median {p['warm']['median_ms']} ms, max {p['warm']['max_ms']} ms"
        )
    print(f"STT phrase_10s beam5 warm median {s['phrase_10s_beam5_warm']['median_ms']} ms")
    t = result["tts"]
    print(
        f"TTS first sentence ({t['sentence_audio_s']} s audio): cold {t['first_sentence_cold_ms']}"
        f" ms, warm median {t['first_sentence_warm']['median_ms']} ms"
    )
    print(f"phoneme 5 s: warm median {result['phoneme']['phrase_5s_warm']['median_ms']} ms")
    print(f"VRAM MB: {json.dumps(vram)}")
    print(f"loop WER: {result['loop']['wer']:.3f}")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
