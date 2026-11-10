"""API connections: routes, keyring, last-4, single active row, test request (plan item 1.3)."""

from __future__ import annotations

import logging
from pathlib import Path

import keyring

from tests.helpers import make_app
from tests.mock_llm import MockLLM
from trainer import keystore
from trainer.logging_setup import REDACTED, RedactingFilter, redact, register_secret


def _create(ctx, mock: MockLLM | None, key: str, name: str = "Mock", **extra):
    body = {
        "name": name,
        "base_url": mock.base_url if mock else "https://api.deepseek.com",
        "model": "mock-model",
        "api_key": key,
        **extra,
    }
    return ctx.client.post("/api/connections", json=body, headers=ctx.auth_headers)


def test_create_stores_key_in_keyring_only(tmp_path: Path, test_key: str) -> None:
    with make_app(tmp_path) as ctx:
        r = _create(ctx, None, test_key)
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["key_last4"] == test_key[-4:]
        assert data["is_active"] is True
        assert "api_key" not in data
        assert test_key not in r.text
        stored = keyring.get_password("EnglishTrainer-test", f"connection-{data['id']}")
        assert stored == test_key
        listing = ctx.client.get("/api/connections")
        assert test_key not in listing.text
    db_bytes = (tmp_path / "trainer.db").read_bytes()
    assert test_key.encode() not in db_bytes


def test_only_one_active_and_activation(tmp_path: Path, test_key: str) -> None:
    with make_app(tmp_path) as ctx:
        a = _create(ctx, None, test_key, name="A").json()
        b = _create(ctx, None, test_key + "x", name="B").json()
        assert a["is_active"] and not b["is_active"]
        r = ctx.client.post(f"/api/connections/{b['id']}/activate", headers=ctx.auth_headers)
        assert r.status_code == 200
        rows = ctx.client.get("/api/connections").json()
        assert [row["is_active"] for row in rows] == [False, True]


def test_delete_removes_key_and_protects_active(tmp_path: Path, test_key: str) -> None:
    with make_app(tmp_path) as ctx:
        a = _create(ctx, None, test_key, name="A").json()
        b = _create(ctx, None, test_key + "b", name="B").json()
        r = ctx.client.delete(f"/api/connections/{a['id']}", headers=ctx.auth_headers)
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "conflict"
        r = ctx.client.delete(f"/api/connections/{b['id']}", headers=ctx.auth_headers)
        assert r.status_code == 204
        assert keyring.get_password("EnglishTrainer-test", f"connection-{b['id']}") is None
        assert len(ctx.client.get("/api/connections").json()) == 1


def test_patch_key_updates_last4(tmp_path: Path, test_key: str) -> None:
    with make_app(tmp_path) as ctx:
        a = _create(ctx, None, test_key).json()
        new_key = test_key[:-4] + "WXYZ"
        r = ctx.client.patch(
            f"/api/connections/{a['id']}",
            json={"api_key": new_key, "name": "Renamed"},
            headers=ctx.auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["key_last4"] == "WXYZ"
        assert r.json()["name"] == "Renamed"
        assert keystore.load_key(a["id"]) == new_key


def test_validation_of_url_and_extra_params(tmp_path: Path, test_key: str) -> None:
    with make_app(tmp_path) as ctx:
        bad_urls = ["http://api.example.com", "ftp://x", "https://user:pw@host", "not a url"]
        for url in bad_urls:
            r = ctx.client.post(
                "/api/connections",
                json={"name": "x", "base_url": url, "model": "m", "api_key": test_key},
                headers=ctx.auth_headers,
            )
            assert r.status_code == 422, url
        ok_local = ctx.client.post(
            "/api/connections",
            json={
                "name": "x",
                "base_url": "http://127.0.0.1:8080/v1",
                "model": "m",
                "api_key": test_key,
            },
            headers=ctx.auth_headers,
        )
        assert ok_local.status_code == 201
        for extra in ({"model": "x"}, {"messages": []}, {"stream": False}):
            r = _create(ctx, None, test_key, extra_params=extra)
            assert r.status_code == 422
        r = _create(ctx, None, test_key, extra_params={"thinking": {"type": "disabled"}})
        assert r.status_code == 201
        assert r.json()["extra_params"] == {"thinking": {"type": "disabled"}}


def test_templates(tmp_path: Path) -> None:
    with make_app(tmp_path) as ctx:
        t = ctx.client.get("/api/connections/templates").json()
        ds = next(x for x in t if x["id"] == "deepseek")
        assert ds["base_url"] == "https://api.deepseek.com"
        assert ds["model"] == "deepseek-flash"
        assert ds["extra_params"] == {"thinking": {"type": "disabled"}}


def test_connection_test_ok_and_401(tmp_path: Path, test_key: str, mock_llm: MockLLM) -> None:
    with make_app(tmp_path) as ctx:
        good = _create(ctx, mock_llm, test_key, name="good").json()
        bad = _create(ctx, mock_llm, test_key + "-wrong", name="bad").json()
        r = ctx.client.post(f"/api/connections/{good['id']}/test", headers=ctx.auth_headers)
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert isinstance(r.json()["latency_ms"], int)
        r = ctx.client.post(f"/api/connections/{bad['id']}/test", headers=ctx.auth_headers)
        res = r.json()
        assert res["ok"] is False
        assert res["code"] == "llm_unauthorized"
        assert test_key not in r.text
        rows = {row["id"]: row for row in ctx.client.get("/api/connections").json()}
        assert rows[good["id"]]["last_test"]["ok"] is True
        assert rows[bad["id"]]["last_test"]["ok"] is False


def test_unsaved_connection_test_stores_nothing(
    tmp_path: Path, test_key: str, mock_llm: MockLLM
) -> None:
    with make_app(tmp_path) as ctx:
        r = ctx.client.post(
            "/api/connections/test",
            json={"base_url": mock_llm.base_url, "model": "m", "api_key": test_key},
            headers=ctx.auth_headers,
        )
        assert r.json()["ok"] is True
        assert ctx.client.get("/api/connections").json() == []
        assert keyring.get_password("EnglishTrainer-test", "connection-1") is None


def test_log_filter_redacts_keys() -> None:
    secret = "sk-test-abcdefghijklmnopqrstuvwxyz012345"
    register_secret(secret)
    assert secret not in redact(f"using key {secret} now")
    assert REDACTED in redact("Authorization: Bearer abcdefghijklmnop")
    assert "sk-live-zzzzzzzzzzzz" not in redact("leaked sk-live-zzzzzzzzzzzz")
    uuid = "0a363e91-1234-4abc-8def-0123456789ab"
    assert uuid in redact(f"turn {uuid}")
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "key=%s", (secret,), None)
    RedactingFilter().filter(record)
    assert secret not in record.getMessage()
