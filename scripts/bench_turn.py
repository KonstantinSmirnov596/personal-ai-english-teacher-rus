"""Latency from the end of a learner turn to the first tutor audio, with the mock LLM.

Runs the real launcher (models on the GPU), sends the turn_basic fixture over the WebSocket and
measures, per turn: core timing (stt, llm first sentence, tts first, first_audio) and the
client-side time from sending turn.audio to receiving the first tutor.sentence with audio.
Mock first-chunk delay 0 ms gives the app's own latency; 500 ms approximates a cloud provider.

    .venv\\Scripts\\python.exe scripts\\bench_turn.py [--runs 5] [--out <json>]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import secrets
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def summarize(values: list[float]) -> dict[str, float]:
    return {"median_ms": round(statistics.median(values), 1), "max_ms": round(max(values), 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--out", default=str(REPO / "docs" / "reports" / "data" / "bench_turn.json"))
    args = ap.parse_args()

    import os

    from trainer import paths

    os.environ.setdefault("TRAINER_MODELS_DIR", str(paths.models_dir()))  # real models, temp data
    os.environ["TRAINER_KEYRING_SERVICE"] = "EnglishTrainer-test"
    import keyring
    import keyring.errors

    from tests.fixtures.make_fixtures import ensure_turns
    from tests.launcher_proc import LauncherProcess
    from tests.mock_llm import MockLLM
    from tests.ws_client import WSClient

    key = "sk-test-" + secrets.token_urlsafe(24)
    mock = MockLLM(key).start()
    data = Path(tempfile.mkdtemp(prefix="bench-turn-"))
    lp = LauncherProcess(data, env={"TRAINER_KEYRING_SERVICE": "EnglishTrainer-test"})
    wav = ensure_turns()["turn_basic"]
    result: dict[str, Any] = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "runs": args.runs,
    }
    try:
        lp.login()
        with lp.http() as http:
            http.post(
                "/api/connections",
                json={"name": "Mock", "base_url": mock.base_url, "model": "m", "api_key": key},
            )
            http.patch("/api/settings", json={"correction_mode": "off"})
            deadline = time.monotonic() + 180
            while (status := http.get("/api/health").json()["status"]) != "ok":
                if status == "error":
                    raise RuntimeError("models failed to load: " + "".join(lp.output[-20:]))
                if time.monotonic() > deadline:
                    raise TimeoutError("models did not load")
                time.sleep(0.5)
        with lp.ws_connect() as ws:
            client = WSClient(ws)
            client.recv()
            client.send({"type": "session.start", "scenario_id": "free_talk"})
            got = client.until(lambda m: m["type"] == "tutor.done", timeout=60)
            client.send({"type": "playback.ended", "turn_id": got[-1].msg["turn_id"]})
            for delay in (0, 500):
                mock.script(first_delay_ms=delay, chunk_delay_ms=30)
                rows: list[dict[str, Any]] = []
                for _ in range(args.runs + 1):
                    _tid, sent = client.send_turn(wav)
                    got = client.until(lambda m: m["type"] == "tutor.sentence", timeout=60)
                    first_audio_client = (got[-1].at - sent) * 1000.0
                    rest = client.until(lambda m: m["type"] == "tutor.done", timeout=60)
                    timing = next(r.msg for r in got + rest if r.msg["type"] == "timing")
                    client.send({"type": "playback.ended", "turn_id": rest[-1].msg["turn_id"]})
                    client.until(
                        lambda m: m["type"] == "state" and m["value"] == "idle", timeout=10
                    )
                    rows.append(
                        {
                            **{
                                k: timing[k]
                                for k in (
                                    "stt_ms",
                                    "llm_first_sentence_ms",
                                    "tts_first_ms",
                                    "first_audio_ms",
                                )
                            },
                            "client_first_audio_ms": round(first_audio_client, 1),
                        }
                    )
                cold, warm = rows[0], rows[1:]
                result[f"mock_delay_{delay}ms"] = {
                    "cold": cold,
                    "first_audio_core": summarize([r["first_audio_ms"] for r in warm]),
                    "first_audio_client": summarize([r["client_first_audio_ms"] for r in warm]),
                    "stt": summarize([r["stt_ms"] for r in warm]),
                    "llm_first_sentence": summarize([r["llm_first_sentence_ms"] for r in warm]),
                    "tts_first": summarize([r["tts_first_ms"] for r in warm]),
                }
    finally:
        lp.stop()
        mock.stop()
        for i in range(1, 3):
            with contextlib.suppress(keyring.errors.PasswordDeleteError):
                keyring.delete_password("EnglishTrainer-test", f"connection-{i}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    for delay in (0, 500):
        r = result[f"mock_delay_{delay}ms"]
        client_fa = r["first_audio_client"]
        print(
            f"mock delay {delay} ms: first audio (client) median {client_fa['median_ms']} ms,"
            f" max {client_fa['max_ms']} ms; core {r['first_audio_core']['median_ms']} ms;"
            f" stt {r['stt']['median_ms']} ms;"
            f" llm 1st sentence {r['llm_first_sentence']['median_ms']} ms;"
            f" tts {r['tts_first']['median_ms']} ms; cold {r['cold']['client_first_audio_ms']} ms"
        )
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
