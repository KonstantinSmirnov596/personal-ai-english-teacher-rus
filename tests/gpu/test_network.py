"""Stage 4.4: over a full scenario the core connects only to 127.0.0.1 — its own port and the
active LLM connection (the mock). Any other address means the app talks to the outside."""

from __future__ import annotations

import contextlib
import json
import secrets
import time
from pathlib import Path

import keyring
import keyring.errors
import pytest

from tests.fixtures.make_fixtures import ensure_turns
from tests.launcher_proc import REPO, LauncherProcess
from tests.mock_llm import MockLLM
from tests.ws_client import WSClient

pytestmark = pytest.mark.gpu

LOCAL = {"127.0.0.1", "localhost", "::1"}


def test_core_talks_only_to_localhost_and_the_llm(tmp_path: Path) -> None:
    key = "sk-test-" + secrets.token_urlsafe(24)
    mock = MockLLM(key).start()
    audit = tmp_path / "net.jsonl"
    lp = LauncherProcess(
        tmp_path / "data",
        env={"TRAINER_NET_AUDIT_LOG": str(audit)},
        unset=("HF_HUB_OFFLINE",),  # as the owner runs it: the launcher sets offline mode itself
        entry=(str(REPO / "tests" / "net_audit_launcher.py"),),
    )
    turns = ensure_turns()
    try:
        lp.login()
        with lp.http() as http:
            conn = http.post(
                "/api/connections",
                json={"name": "Mock", "base_url": mock.base_url, "model": "m", "api_key": key},
            ).json()
            assert http.post(f"/api/connections/{conn['id']}/test").json()["ok"] is True
            deadline = time.monotonic() + 180
            while http.get("/api/health").json()["status"] != "ok":
                assert time.monotonic() < deadline
                time.sleep(0.5)

        with lp.ws_connect() as ws:
            client = WSClient(ws)
            client.recv()
            client.send({"type": "session.start", "scenario_id": "interview_backend"})
            got = client.until(lambda m: m["type"] == "tutor.done", timeout=60)
            opener = got[-1].msg["turn_id"]
            client.send({"type": "playback.ended", "turn_id": opener})
            client.send_turn(turns["turn_accent"])
            client.until(lambda m: m["type"] == "pron.flags", timeout=90)  # loads wav2vec2
            if not any(r.msg["type"] == "corrections" for r in client.log):
                client.until(lambda m: m["type"] == "corrections", timeout=30)
            client.send({"type": "helper.hint", "request_id": "h1"})
            client.until(lambda m: m["type"] == "helper.result", timeout=30)
            client.send({"type": "tutor.replay", "turn_id": opener, "speed": 0.8})
            client.until(lambda m: m["type"] == "tutor.sentence" and m.get("replay"), timeout=30)
            client.send({"type": "session.end"})
            client.until(lambda m: m["type"] == "summary", timeout=60)

        with lp.http() as http:
            r = http.post(
                "/api/pron/assess?phrase_id=p01",
                content=turns["reading_p01_accent"].read_bytes(),
                headers={"Content-Type": "audio/wav"},
            )
            assert r.status_code == 200
            assert http.post("/api/voices/preview", json={"voice": "af_bella"}).status_code == 200
            assert http.get("/api/export?format=json").status_code == 200
            assert http.get("/api/pron/reference?phrase_id=p02").status_code == 200
        assert len(mock.requests) >= 5  # test, tutor, corrector, hint, summary
    finally:
        lp.stop()
        mock.stop()
        for i in range(1, 3):
            with contextlib.suppress(keyring.errors.PasswordDeleteError):
                keyring.delete_password("EnglishTrainer-test", f"connection-{i}")

    events = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert events, "the audit recorded nothing: interception is broken"
    outside = [e for e in events if e["host"] not in LOCAL]
    assert not outside, outside
    # the interception really sees the LLM requests of the core
    llm_connects = [e for e in events if e["kind"] == "connect" and e["port"] == mock.port]
    assert llm_connects
    report = REPO / "docs" / "reports" / "data" / "network_audit.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    summary: dict[str, int] = {}
    for e in events:
        target = "llm (mock)" if e["port"] == mock.port else "other local"
        summary[f"{e['kind']} {e['host']} -> {target}"] = (
            summary.get(f"{e['kind']} {e['host']} -> {target}", 0) + 1
        )
    report.write_text(
        json.dumps({"events": len(events), "outside": 0, "by_kind": summary}, indent=2),
        encoding="utf-8",
    )
