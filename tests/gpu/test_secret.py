"""The API key never leaves the keyring except in requests to the provider (docs/testing.md,
«Секрет»): not in the database, logs, exports, HTTP responses or the core's stdout."""

from __future__ import annotations

import contextlib
import secrets
import time
from pathlib import Path

import keyring
import keyring.errors
import pytest

from tests.fixtures.make_fixtures import ensure_turns
from tests.launcher_proc import LauncherProcess
from tests.mock_llm import MockLLM
from tests.ws_client import WSClient

pytestmark = pytest.mark.gpu


def test_key_marker_stays_in_keyring(tmp_path: Path) -> None:
    marker = "sk-test-MARKER" + secrets.token_hex(16)
    mock = MockLLM(marker).start()
    lp = LauncherProcess(tmp_path)
    try:
        lp.login()
        with lp.http() as http:
            r = http.post(
                "/api/connections",
                json={"name": "Mock", "base_url": mock.base_url, "model": "m", "api_key": marker},
            )
            good_id = r.json()["id"]
            r = http.post(
                "/api/connections",
                json={
                    "name": "Wrong",
                    "base_url": mock.base_url,
                    "model": "m",
                    "api_key": marker + "X",
                },
            )
            bad_id = r.json()["id"]
            http.post(f"/api/connections/{bad_id}/test")  # 401 from the mock
            http.post(f"/api/connections/{good_id}/test")
            deadline = time.monotonic() + 180
            while http.get("/api/health").json()["status"] != "ok":
                assert time.monotonic() < deadline
                time.sleep(0.5)

        with lp.ws_connect() as ws:
            client = WSClient(ws)
            client.recv()
            client.send({"type": "session.start", "scenario_id": "free_talk"})
            got = client.until(lambda m: m["type"] == "tutor.done", timeout=60)
            client.send({"type": "playback.ended", "turn_id": got[-1].msg["turn_id"]})
            client.send_turn(ensure_turns()["turn_basic"])
            client.until(lambda m: m["type"] == "corrections", timeout=60)
            client.send({"type": "session.end"})
            client.until(lambda m: m["type"] == "summary", timeout=60)
            ws_text = "".join(str(r.msg) for r in client.log)
        assert marker not in ws_text

        responses: list[str] = []
        with lp.http() as http:
            paths = [
                "/",
                "/api/health",
                "/api/connections",
                "/api/connections/templates",
                "/api/settings",
                "/api/scenarios",
                "/api/sessions",
                "/api/voices",
                "/js/app.js",
            ]
            sessions = http.get("/api/sessions").json()
            for s in sessions:
                paths += [
                    f"/api/sessions/{s['id']}",
                    f"/api/sessions/{s['id']}/export?format=json",
                    f"/api/sessions/{s['id']}/export?format=csv",
                ]
            for p in paths:
                responses.append(http.get(p).text)
        assert not [p for p, body in zip(paths, responses, strict=True) if marker in body]

        lp.stop()  # flush logs and the WAL before reading files
        for f in tmp_path.rglob("*"):
            if f.is_file() and f.suffix.lower() not in {".wav"}:
                assert marker.encode() not in f.read_bytes(), f"marker found in {f}"
        assert marker not in "".join(lp.output)

        # Where the marker is allowed: the test keyring service and the provider requests.
        assert keyring.get_password("EnglishTrainer-test", f"connection-{good_id}") == marker
        assert any(r["authorization"] == f"Bearer {marker}" for r in mock.requests)
    finally:
        lp.stop()
        mock.stop()
        for i in range(1, 4):
            with contextlib.suppress(keyring.errors.PasswordDeleteError):
                keyring.delete_password("EnglishTrainer-test", f"connection-{i}")
