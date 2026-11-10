"""Launcher: real process, token exchange, exit after the window is gone."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from tests.launcher_proc import LauncherProcess


def test_launcher_serves_and_exits_when_window_closes(tmp_path: Path) -> None:
    lp = LauncherProcess(tmp_path, "--no-models", "--exit-when-idle", "2")
    try:
        assert lp.base.startswith("http://127.0.0.1:")
        assert httpx.get(lp.base + "/api/health", timeout=10).status_code == 401
        lp.login()
        with lp.http() as c:
            assert c.get("/api/health").status_code == 200
            assert c.get("/").status_code == 200
        with lp.ws_connect() as ws:
            hello = json.loads(ws.recv(timeout=10))
            assert hello["type"] == "hello"
        started = time.monotonic()
        code = lp.wait_exit(timeout=15)
        assert code == 0, "".join(lp.output)
        assert time.monotonic() - started < 15
        assert (tmp_path / "trainer.db").exists()
    finally:
        lp.stop()


def test_launcher_keeps_running_before_first_window(tmp_path: Path) -> None:
    lp = LauncherProcess(tmp_path, "--no-models", "--exit-when-idle", "1")
    try:
        assert lp.wait_exit(timeout=3) is None
    finally:
        lp.stop()
