"""Storage: schema, migrations, repositories (plan item 1.2)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from trainer.data_files import scenarios
from trainer.settings import Settings, SettingsPatch
from trainer.storage.db import MIGRATIONS, Database
from trainer.storage.repo import ConnectionRepo, ScenarioRepo, SessionRepo, SettingsRepo

TABLES = {
    "connection",
    "setting",
    "scenario",
    "session",
    "turn",
    "correction",
    "pattern",
    "vocab",
    "pron_attempt",
    "pron_sound",
}


def test_data_dir_and_schema_created(tmp_path: Path) -> None:
    db = Database(tmp_path / "nested" / "trainer.db")
    names = {r["name"] for r in db.all("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names >= TABLES
    assert db.schema_version() == len(MIGRATIONS)
    db.close()


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "t.db"
    Database(path).close()
    db = Database(path)
    rows = db.all("SELECT version FROM schema_version")
    assert [r["version"] for r in rows] == list(range(1, len(MIGRATIONS) + 1))
    db.close()


def test_new_migration_applies_on_existing_db(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "t.db"
    Database(path).close()
    monkeypatch.setattr(
        "trainer.storage.db.MIGRATIONS", [*MIGRATIONS, "CREATE TABLE extra_t (x INTEGER)"]
    )
    db = Database(path)
    assert db.schema_version() == len(MIGRATIONS) + 1
    assert db.one("SELECT name FROM sqlite_master WHERE name = 'extra_t'") is not None
    db.close()


def test_settings_defaults_and_patch(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    repo = SettingsRepo(db)
    assert repo.get() == Settings()
    assert repo.get().correction_mode == "on_click"
    assert repo.get().audio_retention_days == 30
    assert repo.get().voice == "af_heart"
    s = repo.patch(SettingsPatch(level="C1", pause_ms=2000))
    assert s.level == "C1"
    assert repo.get().pause_ms == 2000
    db.close()


def test_connections_single_active(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    repo = ConnectionRepo(db)
    a = repo.create(
        name="A", base_url="https://a", model="m", extra_params={}, json_mode=True, key_last4="1234"
    )
    b = repo.create(
        name="B", base_url="https://b", model="m", extra_params={}, json_mode=True, key_last4="5678"
    )
    assert repo.active()["id"] == a
    repo.activate(b)
    assert repo.active()["id"] == b
    assert sum(1 for r in repo.list() if r["is_active"]) == 1
    db.close()


def test_sessions_turns_corrections_cascade(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    ScenarioRepo(db).seed_builtin(scenarios())
    sessions = SessionRepo(db)
    sid = sessions.create(
        scenario_id="free_talk",
        scenario_title_ru="Свободный разговор",
        level="B1",
        correction_mode="on_click",
        connection_id=None,
        connection_name="",
        model="",
    )
    t1 = sessions.add_turn(session_id=sid, role="tutor", text="Hi!")
    t2 = sessions.add_turn(session_id=sid, role="user", text="I has a cat", words=[{"w": "I"}])
    sessions.add_corrections(
        sid,
        t2,
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
    turns = sessions.turns(sid)
    assert [t["idx"] for t in turns] == [0, 1]
    assert turns[0]["id"] == t1
    assert len(sessions.corrections(sid)) == 1
    sessions.delete(sid)
    assert db.one("SELECT COUNT(*) AS n FROM correction")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM turn")["n"] == 0
    db.close()


def test_builtin_scenarios_seeded_once(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    repo = ScenarioRepo(db)
    repo.seed_builtin(scenarios())
    repo.seed_builtin(scenarios())
    rows = repo.list()
    assert len(rows) == 6
    assert rows[0]["id"] == "free_talk"
    db.close()


def test_no_key_columns_in_schema(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    cols = {r["name"] for r in db.all("PRAGMA table_info(connection)")}
    assert "api_key" not in cols and "key" not in cols
    assert "key_last4" in cols
    db.close()
    with sqlite3.connect(tmp_path / "t.db") as c:
        assert c.execute("PRAGMA foreign_keys").fetchone() is not None
