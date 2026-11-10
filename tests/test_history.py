"""Scenarios and session history routes (plan item 1.9)."""

from __future__ import annotations

import json
from pathlib import Path

from tests.helpers import make_app


def test_builtin_scenarios_and_custom_crud(tmp_path: Path) -> None:
    with make_app(tmp_path) as ctx:
        h = ctx.auth_headers
        rows = ctx.client.get("/api/scenarios").json()
        assert [r["id"] for r in rows][:2] == ["free_talk", "interview_backend"]
        assert len(rows) == 6 and all(r["builtin"] for r in rows)
        assert all(r["openers"] for r in rows)
        assert ctx.client.delete("/api/scenarios/free_talk", headers=h).status_code == 409
        assert (
            ctx.client.patch("/api/scenarios/free_talk", json={"role": "x"}, headers=h).status_code
            == 409
        )
        r = ctx.client.post(
            "/api/scenarios",
            json={
                "title_ru": "Свой",
                "role": "You are a barista.",
                "openers": ["Hi! What can I get you?"],
            },
            headers=h,
        )
        assert r.status_code == 201
        sid = r.json()["id"]
        assert r.json()["builtin"] is False
        r = ctx.client.patch(f"/api/scenarios/{sid}", json={"setting": "A busy cafe."}, headers=h)
        assert r.json()["setting"] == "A busy cafe."
        assert ctx.client.delete(f"/api/scenarios/{sid}", headers=h).status_code == 204
        assert len(ctx.client.get("/api/scenarios").json()) == 6


def _seed_session(ctx) -> tuple[str, str]:
    s = ctx.core.sessions
    sid = s.create(
        scenario_id="free_talk",
        scenario_title_ru="Свободный разговор",
        level="B1",
        correction_mode="on_click",
        connection_id=None,
        connection_name="Mock",
        model="m",
    )
    s.add_turn(session_id=sid, role="tutor", text="Hey, how's it going?")
    audio_rel = f"audio/{sid}/u1.wav"
    audio_path = ctx.core.data_dir / audio_rel
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"RIFF0000WAVE")
    uid = s.add_turn(
        session_id=sid, role="user", text="I has a cat.", turn_id="u1", audio_path=audio_rel
    )
    s.add_corrections(
        sid,
        uid,
        [
            {
                "id": "c1",
                "quote": "I has",
                "fix": "I have",
                "type": "grammar",
                "note_ru": "С I — have.",
                "pattern": "third_person_s",
                "start": 0,
                "end": 5,
            }
        ],
    )
    s.update_turn(uid, corrections_status="ok", better="I have a cat.")
    return sid, uid


def test_sessions_list_detail_export_delete(tmp_path: Path) -> None:
    with make_app(tmp_path) as ctx:
        sid, uid = _seed_session(ctx)
        listing = ctx.client.get("/api/sessions").json()
        assert listing[0]["id"] == sid
        assert listing[0]["user_turns"] == 1 and listing[0]["corrections"] == 1
        detail = ctx.client.get(f"/api/sessions/{sid}").json()
        assert [t["role"] for t in detail["turns"]] == ["tutor", "user"]
        assert detail["turns"][1]["corrections"][0]["fix"] == "I have"
        assert detail["turns"][1]["better"] == "I have a cat."
        assert detail["turns"][1]["has_audio"] is True
        assert ctx.client.get(f"/api/turns/{uid}/audio").status_code == 200

        exp = ctx.client.get(f"/api/sessions/{sid}/export?format=json")
        assert exp.status_code == 200
        assert json.loads(exp.text)["id"] == sid
        csv_exp = ctx.client.get(f"/api/sessions/{sid}/export?format=csv")
        assert "I has a cat." in csv_exp.text and "third_person_s" in csv_exp.text
        assert ctx.client.get(f"/api/sessions/{sid}/export?format=xml").status_code == 422

        audio = tmp_path / "audio" / sid / "u1.wav"
        assert audio.exists()
        assert (
            ctx.client.delete(f"/api/sessions/{sid}", headers=ctx.auth_headers).status_code == 204
        )
        assert not audio.exists()
        assert ctx.client.get(f"/api/sessions/{sid}").status_code == 404
        assert ctx.client.get(f"/api/turns/{uid}/audio").status_code == 404


def test_turn_audio_path_cannot_escape_data_dir(tmp_path: Path) -> None:
    with make_app(tmp_path) as ctx:
        sid, _ = _seed_session(ctx)
        tid = ctx.core.sessions.add_turn(
            session_id=sid, role="user", text="x", audio_path="../../Windows/win.ini"
        )
        assert ctx.client.get(f"/api/turns/{tid}/audio").status_code == 404
