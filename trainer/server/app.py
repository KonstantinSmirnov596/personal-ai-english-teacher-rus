"""FastAPI application: guard, security headers, static window files, API and WebSocket."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from trainer.core import Core
from trainer.i18n_ru import error_text
from trainer.server.guard import COOKIE_NAME, GuardMiddleware

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message_ru: str | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message_ru = message_ru or error_text(code)


def csp(port: int) -> str:
    return (
        "default-src 'self'; "
        f"connect-src 'self' ws://127.0.0.1:{port}; "
        "media-src 'self' blob:; worker-src 'self' blob:; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'"
    )


class SecurityHeadersMiddleware:
    """Adds CSP to HTML, no-store to API, and strips any CORS header defensively."""

    def __init__(self, app: ASGIApp, port: int) -> None:
        self.app = app
        self.csp = csp(port).encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if not k.lower().startswith(b"access-control-")
                ]
                ctype = next((v for k, v in headers if k.lower() == b"content-type"), b"")
                if ctype.startswith(b"text/html"):
                    headers.append((b"content-security-policy", self.csp))
                if path.startswith("/api/"):
                    headers.append((b"cache-control", b"no-store"))
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"referrer-policy", b"no-referrer"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


def create_app(core: Core) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await core.startup()
        try:
            yield
        finally:
            await core.shutdown()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.core = core

    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": exc.code, "message_ru": exc.message_ru}}, status_code=exc.status
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [".".join(str(p) for p in e.get("loc", [])[1:]) for e in exc.errors()]
        return JSONResponse(
            {
                "error": {
                    "code": "validation",
                    "message_ru": error_text("validation"),
                    "fields": fields,
                }
            },
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_req: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "bad_request"
        return JSONResponse(
            {"error": {"code": code, "message_ru": error_text(code)}}, status_code=exc.status_code
        )

    @app.get("/", include_in_schema=False)
    async def index(request: Request, t: str | None = None) -> Response:
        if t is not None:
            if not core.keys.consume_token(t):
                raise ApiError(403, "unauthorized")
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie(
                COOKIE_NAME,
                core.keys.session_secret,
                httponly=True,
                samesite="strict",
                path="/",
                secure=False,
            )
            return resp
        return FileResponse(WEB_DIR / "index.html", media_type="text/html; charset=utf-8")

    from trainer.server import routes_api, ws

    app.include_router(routes_api.router)
    app.add_api_websocket_route("/ws", ws.websocket_endpoint)
    app.mount("/", StaticFiles(directory=str(WEB_DIR)), name="web")

    # Last added runs first: security headers wrap the guard, so denials carry them too.
    app.add_middleware(GuardMiddleware, keys=core.keys)
    app.add_middleware(SecurityHeadersMiddleware, port=core.keys.port)
    return app
