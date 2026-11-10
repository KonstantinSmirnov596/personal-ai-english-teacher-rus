"""A learner turn from WAV through the WebSocket with the mock LLM (plan items 1.6, 1.7).

Runs the real launcher (models on the GPU) and talks the window protocol.
"""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from tests.fixtures.make_fixtures import ensure_turns
from tests.launcher_proc import LauncherProcess
from tests.mock_llm import MockLLM
from tests.ws_client import WSClient

pytestmark = pytest.mark.gpu


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    import secrets

    key = "sk-test-" + secrets.token_urlsafe(24)
    mock = MockLLM(key).start()
    data = tmp_path_factory.mktemp("core")
    lp = LauncherProcess(data)
    try:
        lp.login()
        with lp.http() as http:
            r = http.post(
                "/api/connections",
                json={"name": "Mock", "base_url": mock.base_url, "model": "mock", "api_key": key},
            )
            assert r.status_code == 201, r.text
            deadline = time.monotonic() + 180
            while http.get("/api/health").json()["status"] != "ok":
                assert time.monotonic() < deadline, "models did not load"
                time.sleep(0.5)
        yield {"lp": lp, "mock": mock, "key": key, "fixtures": ensure_turns()}
    finally:
        lp.stop()
        mock.stop()
        import keyring

        for i in range(1, 5):
            with contextlib.suppress(Exception):
                keyring.delete_password("EnglishTrainer-test", f"connection-{i}")


def start_session(client: WSClient) -> str:
    hello = client.recv()
    assert hello.msg["type"] == "hello"
    client.send({"type": "session.start", "scenario_id": "interview_backend"})
    got = client.until(lambda m: m["type"] == "tutor.done", timeout=60)
    started = next(r.msg for r in got if r.msg["type"] == "session.started")
    sentences = [r for r in got if r.msg["type"] == "tutor.sentence"]
    assert sentences and sentences[0].audio
    done = got[-1].msg
    client.send({"type": "playback.ended", "turn_id": done["turn_id"], "interrupted": False})
    client.until(lambda m: m["type"] == "state" and m["value"] == "idle", timeout=10)
    return str(started["session_id"])


def test_turn_through_websocket(env: dict[str, Any]) -> None:
    lp: LauncherProcess = env["lp"]
    mock: MockLLM = env["mock"]
    mock.reset()
    with lp.ws_connect() as ws:
        client = WSClient(ws)
        session_id = start_session(client)
        turn_id, _sent_at = client.send_turn(env["fixtures"]["turn_basic"])
        got = client.until(lambda m: m["type"] == "tutor.done", timeout=60)
        rest = (
            client.until(lambda m: m["type"] == "corrections", timeout=30)
            if not any(r.msg["type"] == "corrections" for r in got)
            else []
        )
        msgs = [r.msg for r in got + rest]
        types = [m["type"] for m in msgs]

        transcript = next(m for m in msgs if m["type"] == "user.transcript")
        assert transcript["turn_id"] == turn_id
        assert "worked on this project" in transcript["text"].lower()
        assert all(0 <= w["p"] <= 1 for w in transcript["words"])

        sentences = [r for r in got if r.msg["type"] == "tutor.sentence"]
        assert sentences, types
        assert sentences[0].msg["audio"]["sample_rate"] == 24000
        assert len(sentences[0].audio or b"") > 24000  # more than 0.5 s of PCM16

        timing = next(m for m in msgs if m["type"] == "timing")
        assert timing["turn_id"] == turn_id
        for k in ("stt_ms", "llm_first_sentence_ms", "tts_first_ms", "first_audio_ms"):
            assert isinstance(timing[k], (int, float)), k

        corrections = next(m for m in msgs if m["type"] == "corrections")
        assert corrections["status"] == "ok"
        item = corrections["items"][0]
        assert item["start"] == 0
        assert transcript["text"][item["start"] : item["end"]] == item["quote"]

        # Protocol order: transcript -> thinking -> first sentence -> speaking -> timing.
        states = [m["value"] for m in msgs if m["type"] == "state"]
        assert states[:3] == ["listening", "transcribing", "thinking"]
        assert "speaking" in states
        assert types.index("user.transcript") < types.index("tutor.sentence")
        assert types.index("tutor.sentence") < types.index("timing")

        client.send(
            {"type": "playback.ended", "turn_id": msgs[-1].get("turn_id", ""), "interrupted": False}
        )

    # What went to the provider: text only, the right prompts, extra params.
    bodies = [r["body"] for r in mock.requests]
    tutor_req = [b for b in bodies if b.get("stream")]
    corr_req = [b for b in bodies if b.get("response_format")]
    assert len(tutor_req) == 1 and len(corr_req) == 1
    assert tutor_req[0]["messages"][0]["role"] == "system"
    assert "engineering manager" in tutor_req[0]["messages"][0]["content"]
    assert tutor_req[0]["messages"][1]["role"] == "assistant"  # the opener
    assert tutor_req[0]["messages"][-1]["role"] == "user"
    learner = json.loads(corr_req[0]["messages"][1]["content"])
    assert learner["learner_said"] == transcript["text"]
    for b in bodies:
        dumped = json.dumps(b)
        assert "audio" not in dumped.lower() or "audio" in json.dumps(b["messages"]).lower()
        assert len(dumped) < 64_000

    with lp.http() as http:
        detail = http.get(f"/api/sessions/{session_id}").json()
    roles = [t["role"] for t in detail["turns"]]
    assert roles[:3] == ["tutor", "user", "tutor"]
    assert detail["turns"][1]["corrections"][0]["pattern"] == "for_since"


def test_history_prefix_is_stable(env: dict[str, Any]) -> None:
    lp: LauncherProcess = env["lp"]
    mock: MockLLM = env["mock"]
    mock.reset()
    with lp.ws_connect() as ws:
        client = WSClient(ws)
        start_session(client)
        for _ in range(2):
            client.send_turn(env["fixtures"]["turn_basic"])
            got = client.until(lambda m: m["type"] == "tutor.done", timeout=60)
            client.send(
                {"type": "playback.ended", "turn_id": got[-1].msg["turn_id"], "interrupted": False}
            )
    tutor_req = [r["body"] for r in mock.requests if r["body"].get("stream")]
    assert len(tutor_req) == 2
    first = json.dumps(tutor_req[0]["messages"], ensure_ascii=False)
    second = json.dumps(
        tutor_req[1]["messages"][: len(tutor_req[0]["messages"])], ensure_ascii=False
    )
    assert first == second
    assert len(tutor_req[1]["messages"]) == len(tutor_req[0]["messages"]) + 2


def test_interrupt_stops_reply_but_not_corrector(env: dict[str, Any]) -> None:
    lp: LauncherProcess = env["lp"]
    mock: MockLLM = env["mock"]
    mock.reset()
    long_text = " ".join(f"This is sentence number {i} of a long answer." for i in range(1, 30))
    with lp.ws_connect() as ws:
        client = WSClient(ws)
        start_session(client)
        mock.script(text=long_text, chunk_delay_ms=150)
        client.send_turn(env["fixtures"]["turn_basic"])
        client.until(lambda m: m["type"] == "tutor.sentence", timeout=60)
        client.send({"type": "interrupt"})
        got = client.until(lambda m: m["type"] == "tutor.done", timeout=30)
        done = got[-1].msg
        assert done["interrupted"] is True
        after = client.collect(4.0)
        assert not [r for r in after if r.msg["type"] in ("tutor.sentence", "tutor.delta")]
        all_msgs = [r.msg for r in got + after]
        assert any(m["type"] == "state" and m["value"] == "idle" for m in all_msgs)
        everything = [r.msg for r in client.log]
        assert any(m["type"] == "corrections" and m["status"] == "ok" for m in everything)
    stream_reqs = [r for r in mock.requests if r["body"].get("stream")]
    assert stream_reqs  # the tutor request was made and then abandoned


def test_silence_and_short_turns(env: dict[str, Any]) -> None:
    lp: LauncherProcess = env["lp"]
    mock: MockLLM = env["mock"]
    mock.reset()
    with lp.ws_connect() as ws:
        client = WSClient(ws)
        start_session(client)
        before = len(mock.requests)
        client.send_turn(env["fixtures"]["silence_5s"])
        got = client.until(lambda m: m["type"] == "user.transcript", timeout=30)
        assert got[-1].msg["text"] == ""
        client.until(lambda m: m["type"] == "state" and m["value"] == "idle", timeout=10)
        client.send_turn(env["fixtures"]["noise_5s"])
        got = client.until(lambda m: m["type"] == "user.transcript", timeout=30)
        assert got[-1].msg["text"] == ""
        client.until(lambda m: m["type"] == "state" and m["value"] == "idle", timeout=10)
        assert len(mock.requests) == before
        client.send_turn(env["fixtures"]["too_short"])
        got = client.until(lambda m: m["type"] == "error", timeout=10)
        assert got[-1].msg["code"] == "audio_too_short"


def test_unauthorized_connection_reports_error(env: dict[str, Any]) -> None:
    lp: LauncherProcess = env["lp"]
    mock: MockLLM = env["mock"]
    mock.reset()
    with lp.ws_connect() as ws:
        client = WSClient(ws)
        start_session(client)
        mock.script(error_status=401)
        turn_id, _ = client.send_turn(env["fixtures"]["turn_basic"])
        got = client.until(lambda m: m["type"] == "error", timeout=60)
        err = got[-1].msg
        assert err["code"] == "llm_unauthorized"
        assert err["turn_id"] == turn_id
        assert err["retryable"] is False
        client.until(lambda m: m["type"] == "state" and m["value"] == "idle", timeout=10)
        # Retry after the provider recovers: the same learner turn gets a reply.
        mock.script()
        client.send({"type": "turn.retry", "turn_id": turn_id})
        got = client.until(lambda m: m["type"] == "tutor.done", timeout=60)
        assert got[-1].msg["interrupted"] is False
        assert str(uuid.UUID(got[-1].msg["turn_id"]))
