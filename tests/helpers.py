"""Shared test helpers: in-process app with a temporary data directory."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from starlette.testclient import TestClient

from trainer.core import Core
from trainer.llm.client import Timeouts
from trainer.server.app import create_app
from trainer.server.guard import COOKIE_NAME, AccessKeys

TEST_PORT = 47999


@dataclass
class AppCtx:
    client: TestClient
    core: Core
    keys: AccessKeys
    origin: str
    base_url: str

    def login(self) -> None:
        r = self.client.get(f"/?t={self.keys.launch_token}", follow_redirects=False)
        assert r.status_code == 303
        self.client.cookies.set(COOKIE_NAME, self.keys.session_secret)

    @property
    def auth_headers(self) -> dict[str, str]:
        return {"Origin": self.origin}


@contextmanager
def make_app(
    data_dir: Path, *, login: bool = True, timeouts: Timeouts | None = None
) -> Iterator[AppCtx]:
    keys = AccessKeys(port=TEST_PORT)
    core = Core(data_dir=data_dir, keys=keys, load_models=False, llm_timeouts=timeouts)
    app = create_app(core)
    base = f"http://127.0.0.1:{TEST_PORT}"
    with TestClient(app, base_url=base) as client:
        ctx = AppCtx(client=client, core=core, keys=keys, origin=base, base_url=base)
        if login:
            ctx.login()
        yield ctx
