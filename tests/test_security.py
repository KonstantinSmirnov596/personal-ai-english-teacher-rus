"""Protection of the local server (docs/testing.md, «Проверки защиты»)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.helpers import TEST_PORT, make_app
from trainer.server.guard import COOKIE_NAME

WEB = Path(__file__).resolve().parent.parent / "web"


def test_health_requires_cookie(tmp_path: Path) -> None:
    with make_app(tmp_path, login=False) as ctx:
        assert ctx.client.get("/api/health").status_code == 401


def test_foreign_host_rejected_even_with_cookie(tmp_path: Path) -> None:
    with make_app(tmp_path) as ctx:
        r = ctx.client.get("/api/health", headers={"Host": "evil.example"})
        assert r.status_code == 421
        r = ctx.client.get("/api/health", headers={"Host": f"evil.example:{TEST_PORT}"})
        assert r.status_code == 421


def test_foreign_origin_rejected(tmp_path: Path) -> None:
    with make_app(tmp_path) as ctx:
        r = ctx.client.get("/api/health", headers={"Origin": "http://evil.example"})
        assert r.status_code == 403
        assert ctx.client.get("/api/health").status_code == 200
        r = ctx.client.get("/api/health", headers={"Origin": ctx.origin})
        assert r.status_code == 200


def test_state_change_requires_origin(tmp_path: Path) -> None:
    with make_app(tmp_path) as ctx:
        r = ctx.client.patch("/api/settings", json={"level": "B2"})
        assert r.status_code == 403
        r = ctx.client.patch("/api/settings", json={"level": "B2"}, headers=ctx.auth_headers)
        assert r.status_code == 200


def test_localhost_host_and_origin_allowed(tmp_path: Path) -> None:
    with make_app(tmp_path) as ctx:
        r = ctx.client.get(
            "/api/health",
            headers={
                "Host": f"localhost:{TEST_PORT}",
                "Origin": f"http://localhost:{TEST_PORT}",
            },
        )
        assert r.status_code == 200


def test_launch_token_is_one_time(tmp_path: Path) -> None:
    with make_app(tmp_path, login=False) as ctx:
        token = ctx.keys.launch_token
        r = ctx.client.get(f"/?t={token}", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"
        set_cookie = r.headers["set-cookie"]
        assert f"{COOKIE_NAME}=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=strict" in set_cookie or "SameSite=Strict" in set_cookie
        assert "Path=/" in set_cookie
        assert ctx.client.get(f"/?t={token}", follow_redirects=False).status_code == 403
        assert ctx.client.get("/?t=wrong", follow_redirects=False).status_code == 403


def test_wrong_cookie_rejected(tmp_path: Path) -> None:
    with make_app(tmp_path, login=False) as ctx:
        ctx.client.cookies.set(COOKIE_NAME, "not-the-secret")
        assert ctx.client.get("/api/health").status_code == 401
        assert ctx.client.get("/").status_code == 401
        assert ctx.client.get("/css/tokens.css").status_code == 401


def test_html_has_csp_and_no_cors(tmp_path: Path) -> None:
    with make_app(tmp_path) as ctx:
        r = ctx.client.get("/")
        assert r.status_code == 200
        csp = r.headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert f"connect-src 'self' ws://127.0.0.1:{TEST_PORT}" in csp
        assert "frame-ancestors 'none'" in csp
        responses = [
            r,
            ctx.client.get("/api/health", headers={"Origin": ctx.origin}),
            ctx.client.options(
                "/api/settings",
                headers={"Origin": ctx.origin, "Access-Control-Request-Method": "PATCH"},
            ),
            ctx.client.get("/api/health", headers={"Origin": "http://evil.example"}),
            ctx.client.get("/api/connections"),
        ]
        for resp in responses:
            assert not [h for h in resp.headers if h.lower().startswith("access-control-")]


def _ws_rejected(ctx, headers: dict[str, str]) -> bool:
    try:
        with ctx.client.websocket_connect(f"ws://127.0.0.1:{TEST_PORT}/ws", headers=headers) as ws:
            ws.receive_text()
    except WebSocketDisconnect:
        return True
    return False


def test_websocket_guard(tmp_path: Path) -> None:
    with make_app(tmp_path, login=False) as ctx:
        assert _ws_rejected(ctx, {"Origin": ctx.origin})  # no cookie
        ctx.login()
        assert _ws_rejected(ctx, {"Origin": "http://evil.example"})
        assert _ws_rejected(ctx, {})  # Origin is mandatory for WebSocket
        assert _ws_rejected(ctx, {"Origin": ctx.origin, "Host": "evil.example"})
        with ctx.client.websocket_connect(
            f"ws://127.0.0.1:{TEST_PORT}/ws", headers={"Origin": ctx.origin}
        ) as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            assert hello["protocol"] == 1


def test_second_websocket_replaces_first(tmp_path: Path) -> None:
    with make_app(tmp_path) as ctx:
        h = {"Origin": ctx.origin}
        with ctx.client.websocket_connect(f"ws://127.0.0.1:{TEST_PORT}/ws", headers=h) as first:
            assert first.receive_json()["type"] == "hello"
            with ctx.client.websocket_connect(
                f"ws://127.0.0.1:{TEST_PORT}/ws", headers=h
            ) as second:
                assert second.receive_json()["type"] == "hello"
                with pytest.raises(WebSocketDisconnect) as info:
                    first.receive_json()
                assert info.value.code == 4001


_URL = re.compile(r"https?://")


def test_web_files_have_no_external_urls() -> None:
    offenders: list[str] = []
    for path in WEB.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {
            ".html",
            ".css",
            ".js",
            ".mjs",
            ".json",
        }:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _URL.search(line) and "license" not in line.lower():
                offenders.append(f"{path.relative_to(WEB)}:{lineno}")
    assert not offenders
