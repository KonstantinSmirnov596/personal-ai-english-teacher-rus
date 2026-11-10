"""End-to-end fixtures: mock LLM, real launcher (--no-window), Edge through Playwright.

The browser gets a looping WAV as its microphone (docs/testing.md). The launch URL carries a
one-time token, so one persistent browser context is shared by the tests of a module.
"""

from __future__ import annotations

import secrets
import shutil
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.fixtures.make_fixtures import ensure_turns
from tests.launcher_proc import LauncherProcess
from tests.mock_llm import MockLLM
from trainer.server.guard import COOKIE_NAME

ARTIFACTS = Path(__file__).resolve().parents[2] / "test-results" / "e2e"


class E2E:
    def __init__(self, lp: LauncherProcess, mock: MockLLM, key: str, context: Any) -> None:
        self.lp = lp
        self.mock = mock
        self.key = key
        self.context = context
        self.page: Any = None

    def new_page(self) -> Any:
        if self.page is not None:
            self.page.close()
        self.page = self.context.new_page()
        self.page.on("console", lambda m: self.console.append(f"{m.type}: {m.text}"))
        self.console: list[str] = []
        return self.page

    def http(self) -> httpx.Client:
        cookie = next(c["value"] for c in self.context.cookies() if c["name"] == COOKIE_NAME)
        return httpx.Client(
            base_url=self.lp.base,
            headers={"Origin": self.lp.base, "Cookie": f"{COOKIE_NAME}={cookie}"},
            timeout=30,
        )

    def wait_models(self, timeout: float = 180) -> None:
        deadline = time.monotonic() + timeout
        with self.http() as c:
            while c.get("/api/health").json()["status"] != "ok":
                assert time.monotonic() < deadline, "models did not load"
                time.sleep(0.5)

    def screenshot(self, name: str) -> Path:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        path = ARTIFACTS / f"{name}.png"
        self.page.screenshot(path=str(path), full_page=True)
        return path


def launch_context(pw: Any, wav: Path, profile: Path) -> Any:
    return pw.chromium.launch_persistent_context(
        str(profile),
        channel="msedge",
        headless=True,
        viewport={"width": 1180, "height": 860},
        args=[
            "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream",
            f"--use-file-for-fake-audio-capture={wav}",
            "--mute-audio",
            "--no-first-run",
        ],
    )


@pytest.fixture(scope="module")
def e2e(tmp_path_factory: pytest.TempPathFactory) -> Iterator[E2E]:
    from playwright.sync_api import sync_playwright

    key = "sk-test-" + secrets.token_urlsafe(24)
    mock = MockLLM(key).start()
    lp = LauncherProcess(tmp_path_factory.mktemp("core"))
    profile = Path(tempfile.mkdtemp(prefix="e2e-edge-"))
    wav = ensure_turns()["turn_basic"]
    try:
        with sync_playwright() as pw:
            context = launch_context(pw, wav, profile)
            env = E2E(lp, mock, key, context)
            page = env.new_page()
            page.goto(lp.url)  # one-time token -> cookie -> redirect to /
            page.wait_for_url(lp.base + "/")
            yield env
            context.close()
    finally:
        lp.stop()
        mock.stop()
        shutil.rmtree(profile, ignore_errors=True)
        import contextlib

        import keyring
        import keyring.errors

        for i in range(1, 6):
            with contextlib.suppress(keyring.errors.PasswordDeleteError):
                keyring.delete_password("EnglishTrainer-test", f"connection-{i}")
