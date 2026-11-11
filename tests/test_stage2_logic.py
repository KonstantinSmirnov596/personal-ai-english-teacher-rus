"""Stage 2 logic without models: helpers, summary, patterns, vocabulary, data maintenance."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import keyring
import pytest

from tests.helpers import make_app
from tests.mock_llm import MockLLM
from trainer.conversation import SessionCtx
from trainer.helpers import (
    HintOut,
    answer_matches,
    handle_helper,
    normalize_answer,
)
from trainer.maintenance import cleanup_audio, delete_all_data
from trainer.patterns import focus_lines, update_after_session
from trainer.protocol import HelperHint, HelperHowToSay, HelperTranslate
from trainer.summary import build_summary, phrase_in_lines


class FakeConv:
    def __init__(self, core: Any, session: SessionCtx | None) -> None:
        self.core = core
        self.session = session
        self.sent: list[Any] = []
        self.errors: list[dict[str, Any]] = []
        self.replay_cache: dict[Any, Any] = {}
        self.last_summary: dict[str, Any] | None = None

    async def send(self, msg: Any, audio: bytes | None = None) -> None:
        self.sent.append(msg)

    async def error(self, code: str, **kw: Any) -> None:
        self.errors.append({"code": code, **kw})


def _connect(ctx, mock: MockLLM, key: str) -> None:
    r = ctx.client.post(
        "/api/connections",
        json={"name": "Mock", "base_url": mock.base_url, "model": "m", "api_key": key},
        headers=ctx.auth_headers,
    )
    assert r.status_code == 201


def _new_session(core: Any, user_texts: list[tuple[str, list[str]]]) -> str:
    """A finished session with checked learner turns; each turn may carry pattern keys."""
    s = core.sessions
    sid = s.create(
        scenario_id="free_talk",
        scenario_title_ru="Свободный разговор",
        level="B1",
        correction_mode="on_click",
        connection_id=None,
        connection_name="",
        model="",
    )
    s.add_turn(session_id=sid, role="tutor", text="How long have you been on this project?")
    for i, (text, keys) in enumerate(user_texts):
        tid = s.add_turn(session_id=sid, role="user", text=text)
        items = [
            {
                "id": f"{sid}-{i}-{k}",
                "quote": text.split()[0],
                "fix": "x",
                "type": "grammar",
                "note_ru": "Пояснение.",
                "pattern": k,
                "start": 0,
                "end": 1,
            }
            for k in keys
        ]
        if items:
            s.add_corrections(sid, tid, items)
        s.update_turn(tid, corrections_status="ok" if items else "empty")
    return sid


def test_pattern_focus_after_three_sessions_and_close_after_three_clean(tmp_path: Path) -> None:
    with make_app(tmp_path) as ctx:
        core = ctx.core
        for _ in range(3):
            sid = _new_session(core, [("I work here since two years", ["for_since"])])
            update_after_session(core, sid)
        row = core.db.one("SELECT * FROM pattern WHERE key = 'for_since'")
        assert row["state"] == "focus" and row["session_streak"] == 3
        lines = focus_lines(core)
        assert any("for and since" in line for line in lines)
        system_prompt_has_focus = lines and "PRACTICE FOCUS"
        assert system_prompt_has_focus
        for n in range(3):
            sid = _new_session(core, [("I like my job", [])])
            update_after_session(core, sid)
            row = core.db.one("SELECT * FROM pattern WHERE key = 'for_since'")
            assert row["state"] == ("closed" if n == 2 else "focus")
        assert focus_lines(core) == []
        listing = ctx.client.get("/api/patterns").json()
        assert listing[0]["key"] == "for_since" and listing[0]["state"] == "closed"


def test_session_without_checked_turns_does_not_count_as_clean(tmp_path: Path) -> None:
    with make_app(tmp_path) as ctx:
        core = ctx.core
        sid = _new_session(core, [("I work here since two years", ["for_since"])])
        update_after_session(core, sid)
        for _ in range(4):
            sid = _new_session(core, [])
            update_after_session(core, sid)
        assert core.db.one("SELECT state FROM pattern WHERE key = 'for_since'")["state"] == "new"


def test_tutor_prompt_contains_focus(tmp_path: Path) -> None:
    from trainer.data_files import scenarios
    from trainer.prompts.tutor import build_system_prompt

    prompt = build_system_prompt(
        level="B1",
        scenario=scenarios()[0],
        recast=True,
        focus_lines=["for and since: ask how long."],
    )
    assert "PRACTICE FOCUS" in prompt and "for and since" in prompt
    assert "PRACTICE FOCUS" not in build_system_prompt(
        level="B1", scenario=scenarios()[0], recast=False, focus_lines=[]
    )


def test_summary_with_mock(tmp_path: Path, mock_llm: MockLLM, test_key: str) -> None:
    with make_app(tmp_path) as ctx:
        _connect(ctx, mock_llm, test_key)
        core = ctx.core
        sid = _new_session(
            core,
            [("I work here since two years", ["for_since"]), ("We was happy", ["was_were"])],
        )
        session = SessionCtx(
            id=sid, scenario={}, level="B1", correction_mode="on_click", system_prompt=""
        )
        summary = ctx.client.portal.call(build_summary, core, session)
        assert summary["key_takeaway"]["title"] == "for / since"
        assert summary["key_takeaway"]["pattern"] == "for_since"
        assert summary["exercise"]["answer_en"]
        assert len(summary["corrections"]) == 2
        assert {p["key"] for p in summary["patterns"]} == {"for_since", "was_were"}
        assert summary["phrases"] and summary["phrases"][0]["en"] == "on this project"
        assert summary["sounds"] == []


def test_summary_without_corrections_has_no_takeaway(
    tmp_path: Path, mock_llm: MockLLM, test_key: str
) -> None:
    with make_app(tmp_path) as ctx:
        _connect(ctx, mock_llm, test_key)
        sid = _new_session(ctx.core, [("I like my job", [])])
        session = SessionCtx(
            id=sid, scenario={}, level="B1", correction_mode="on_click", system_prompt=""
        )
        summary = ctx.client.portal.call(build_summary, ctx.core, session)
        assert summary["key_takeaway"] is None and summary["exercise"] is None


def test_phrase_validation() -> None:
    lines = ["Could you walk me through a project you're proud of?"]
    assert phrase_in_lines("walk me through", lines) == lines[0]
    assert phrase_in_lines("walked me through", lines) == lines[0]
    assert phrase_in_lines("break the ice", lines) is None


def test_exercise_answer_normalization() -> None:
    answers = ["I have worked here for three years.", "I've been working here for three years."]
    assert answer_matches("i've worked here for three years", answers)
    assert answer_matches("I have been working here for three years!", answers)
    assert not answer_matches("I work here since three years", answers)
    assert normalize_answer("Don't") == "do not"


def test_text_helpers(tmp_path: Path, mock_llm: MockLLM, test_key: str) -> None:
    with make_app(tmp_path) as ctx:
        _connect(ctx, mock_llm, test_key)
        core = ctx.core
        sid = _new_session(core, [("I like my job", [])])
        tutor_turn = core.sessions.turns(sid)[0]["id"]
        session = SessionCtx(
            id=sid,
            scenario={"title_en": "Free talk", "role": "friend"},
            level="B1",
            correction_mode="on_click",
            system_prompt="",
            history=[
                {"role": "assistant", "content": "How was your weekend?"},
                {"role": "user", "content": "It was fine.\n[asr_unsure: fine]"},
            ],
            last_tutor_text="How was your weekend?",
        )
        conv = FakeConv(core, session)
        call = ctx.client.portal.call
        call(handle_helper, conv, HelperHint(type="helper.hint", request_id="r1"), None)
        translate = HelperTranslate(type="helper.translate", request_id="r2", turn_id=tutor_turn)
        call(handle_helper, conv, translate, None)
        how = HelperHowToSay(type="helper.how_to_say", request_id="r3", text_ru="Можно вопрос?")
        call(handle_helper, conv, how, None)
        assert not conv.errors, conv.errors
        by_id = {m.request_id: m for m in conv.sent}
        assert by_id["r1"].kind == "hint" and len(by_id["r1"].data["options"]) == 2
        assert by_id["r2"].kind == "translate" and by_id["r2"].data["ru"]
        assert by_id["r3"].data["en"] == "I'd like to ask a question."
        hint_req = next(
            r for r in mock_llm.requests if "Suggest replies" in r["body"]["messages"][0]["content"]
        )
        assert "[asr_unsure" not in hint_req["body"]["messages"][0]["content"]
        assert hint_req["body"]["response_format"] == {"type": "json_object"}


def test_hint_with_cyrillic_is_rejected_after_retry(
    tmp_path: Path, mock_llm: MockLLM, test_key: str
) -> None:
    with make_app(tmp_path) as ctx:
        _connect(ctx, mock_llm, test_key)
        sid = _new_session(ctx.core, [])
        session = SessionCtx(
            id=sid, scenario={}, level="B1", correction_mode="on_click", system_prompt=""
        )
        conv = FakeConv(ctx.core, session)
        mock_llm.script(invalid_json=True)
        hint = HelperHint(type="helper.hint", request_id="r9")
        ctx.client.portal.call(handle_helper, conv, hint, None)
        assert conv.errors and conv.errors[0]["code"] == "llm_bad_response"
        assert conv.errors[0]["request_id"] == "r9"
    assert HintOut.model_validate({"options": [{"en": "Hi"}]})


def test_vocab_spaced_repetition(tmp_path: Path) -> None:
    with make_app(tmp_path) as ctx:
        h = ctx.auth_headers
        v = ctx.client.post(
            "/api/vocab",
            json={"phrase": "walk me through", "translation_ru": "расскажите по порядку"},
            headers=h,
        ).json()
        assert v["interval_days"] == 1
        assert ctx.client.get("/api/vocab/due").json() == []
        ctx.core.db.execute("UPDATE vocab SET next_review_at = '2000-01-01T00:00:00.000000Z'")
        assert len(ctx.client.get("/api/vocab/due").json()) == 1
        r = ctx.client.patch(f"/api/vocab/{v['id']}", json={"result": "good"}, headers=h).json()
        assert r["reviews"] == 1 and r["interval_days"] == 3
        r = ctx.client.patch(f"/api/vocab/{v['id']}", json={"result": "good"}, headers=h).json()
        assert r["interval_days"] == 7
        r = ctx.client.patch(f"/api/vocab/{v['id']}", json={"result": "again"}, headers=h).json()
        assert r["reviews"] == 0 and r["interval_days"] == 1
        dup = ctx.client.post("/api/vocab", json={"phrase": "Walk me through"}, headers=h).json()
        assert dup["id"] == v["id"]
        assert (
            ctx.client.post("/api/vocab", json={"phrase": "привет"}, headers=h).status_code == 422
        )
        assert ctx.client.delete(f"/api/vocab/{v['id']}", headers=h).status_code == 204


def test_cleanup_removes_audio_older_than_retention(tmp_path: Path) -> None:
    with make_app(tmp_path) as ctx:
        core = ctx.core
        sid = _new_session(core, [])
        old_rel, new_rel = f"audio/{sid}/old.wav", f"audio/{sid}/new.wav"
        for rel in (old_rel, new_rel):
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_bytes(b"RIFF")
        old_tid = core.sessions.add_turn(session_id=sid, role="user", text="a", audio_path=old_rel)
        new_tid = core.sessions.add_turn(session_id=sid, role="user", text="b", audio_path=new_rel)
        old_time = (datetime.now(UTC) - timedelta(days=31)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        core.db.execute("UPDATE turn SET created_at = ? WHERE id = ?", (old_time, old_tid))
        assert cleanup_audio(core) == 1
        assert not (tmp_path / old_rel).exists() and (tmp_path / new_rel).exists()
        assert core.sessions.get_turn(old_tid)["audio_path"] is None
        assert core.sessions.get_turn(new_tid)["audio_path"] == new_rel
        r = ctx.client.patch(
            "/api/settings", json={"audio_retention_days": 0}, headers=ctx.auth_headers
        )
        assert r.status_code == 200
        assert not (tmp_path / new_rel).exists()


def test_delete_all_data_keeps_keys_without_flag(tmp_path: Path, test_key: str) -> None:
    with make_app(tmp_path) as ctx:
        h = ctx.auth_headers
        conn = ctx.client.post(
            "/api/connections",
            json={
                "name": "K",
                "base_url": "https://api.deepseek.com",
                "model": "m",
                "api_key": test_key,
            },
            headers=h,
        ).json()
        sid = _new_session(ctx.core, [("I has a cat", ["third_person_s"])])
        update_after_session(ctx.core, sid)
        ctx.client.post("/api/vocab", json={"phrase": "a solid stretch"}, headers=h)
        (tmp_path / "audio" / sid).mkdir(parents=True, exist_ok=True)
        (tmp_path / "audio" / sid / "x.wav").write_bytes(b"RIFF")
        assert (
            ctx.client.request(
                "DELETE", "/api/data", json={"confirm": "nope"}, headers=h
            ).status_code
            == 422
        )
        r = ctx.client.request("DELETE", "/api/data", json={"confirm": "DELETE"}, headers=h)
        assert r.status_code == 200 and r.json()["removed_keys"] == 0
        assert ctx.client.get("/api/sessions").json() == []
        assert ctx.client.get("/api/vocab").json() == []
        assert ctx.client.get("/api/patterns").json() == []
        assert not (tmp_path / "audio").exists()
        assert len(ctx.client.get("/api/connections").json()) == 1
        assert keyring.get_password("EnglishTrainer-test", f"connection-{conn['id']}") == test_key
        r = ctx.client.request(
            "DELETE", "/api/data", json={"confirm": "DELETE", "include_keys": True}, headers=h
        )
        assert r.json()["removed_keys"] == 1
        assert ctx.client.get("/api/connections").json() == []
        assert keyring.get_password("EnglishTrainer-test", f"connection-{conn['id']}") is None


def test_export_all(tmp_path: Path) -> None:
    with make_app(tmp_path) as ctx:
        _new_session(ctx.core, [("I has a cat", ["third_person_s"])])
        j = ctx.client.get("/api/export?format=json").json()
        assert len(j["sessions"]) == 1 and "vocab" in j
        c = ctx.client.get("/api/export?format=csv").text
        assert "I has a cat" in c


@pytest.mark.skipif(
    os.environ.get("TRAINER_KEYRING_SERVICE") != "EnglishTrainer-test", reason="isolation"
)
def test_delete_all_direct_call(tmp_path: Path) -> None:
    with make_app(tmp_path) as ctx:
        assert delete_all_data(ctx.core, include_keys=False)["ok"] is True


def test_synthesis_service_rejects_cyrillic_before_any_model_work() -> None:
    """Russian text never reaches synthesis: the service refuses it without loading the model."""
    from trainer.speech.tts import CyrillicTextError, SpeechSynthesizer

    tts = SpeechSynthesizer(device="cpu")
    with pytest.raises(CyrillicTextError):
        tts.synthesize("Скажите это по-английски.")
    assert not tts.loaded
