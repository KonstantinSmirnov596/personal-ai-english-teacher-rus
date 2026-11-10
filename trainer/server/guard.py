"""Access guard for the local server: Host, launch cookie and Origin on every HTTP request and
WebSocket handshake (docs/protocol.md, «Запуск и допуск»).

The only request admitted without the cookie is ``GET /?t=<launch token>``, which the route
exchanges for the cookie once.
"""

from __future__ import annotations

import hmac
import json
import secrets
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Receive, Scope, Send

COOKIE_NAME = "trainer_session"
STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class AccessKeys:
    """Per-launch secrets: the one-time launch token and the session cookie value."""

    port: int
    launch_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    session_secret: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    token_used: bool = False

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return frozenset({f"127.0.0.1:{self.port}", f"localhost:{self.port}"})

    @property
    def allowed_origins(self) -> frozenset[str]:
        return frozenset({f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"})

    def consume_token(self, token: str) -> bool:
        if self.token_used or not hmac.compare_digest(token, self.launch_token):
            return False
        self.token_used = True
        return True

    def cookie_ok(self, value: str | None) -> bool:
        return value is not None and hmac.compare_digest(value, self.session_secret)


def _headers(scope: Scope) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in scope.get("headers", []):
        out[k.decode("latin-1").lower()] = v.decode("latin-1")
    return out


def _cookie(headers: dict[str, str]) -> str | None:
    raw = headers.get("cookie")
    if not raw:
        return None
    jar: SimpleCookie = SimpleCookie()
    try:
        jar.load(raw)
    except Exception:
        return None
    morsel = jar.get(COOKIE_NAME)
    return morsel.value if morsel else None


class GuardMiddleware:
    def __init__(self, app: ASGIApp, keys: AccessKeys) -> None:
        self.app = app
        self.keys = keys

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = _headers(scope)
        is_ws = scope["type"] == "websocket"
        method = "GET" if is_ws else scope.get("method", "GET")

        if headers.get("host") not in self.keys.allowed_hosts:
            await self._deny(scope, receive, send, 421, "misdirected_request")
            return

        if not is_ws and method == "GET" and scope.get("path") == "/" and self._has_token(scope):
            await self.app(scope, receive, send)  # the route checks and consumes the token
            return

        if not self.keys.cookie_ok(_cookie(headers)):
            await self._deny(scope, receive, send, 401, "unauthorized")
            return

        origin = headers.get("origin")
        if origin is not None and origin not in self.keys.allowed_origins:
            await self._deny(scope, receive, send, 403, "forbidden_origin")
            return
        if origin is None and (is_ws or method in STATE_CHANGING):
            await self._deny(scope, receive, send, 403, "origin_required")
            return

        await self.app(scope, receive, send)

    @staticmethod
    def _has_token(scope: Scope) -> bool:
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        return "t" in query

    @staticmethod
    async def _deny(scope: Scope, receive: Receive, send: Send, status: int, code: str) -> None:
        if scope["type"] == "websocket":
            # Rejecting before accept makes the server answer the handshake with HTTP 403.
            await receive()
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"error": {"code": code, "message_ru": "Доступ запрещён"}}).encode()
        response_headers: list[Any] = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"),
        ]
        await send({"type": "http.response.start", "status": status, "headers": response_headers})
        await send({"type": "http.response.body", "body": body})
