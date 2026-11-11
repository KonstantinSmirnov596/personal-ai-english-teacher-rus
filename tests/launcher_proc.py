"""Runs the real launcher (``--no-window``) in a subprocess for integration tests."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from trainer.server.guard import COOKIE_NAME

REPO = Path(__file__).resolve().parent.parent


class LauncherProcess:
    def __init__(
        self,
        data_dir: Path,
        *extra_args: str,
        env: dict[str, str] | None = None,
        unset: tuple[str, ...] = (),
        entry: tuple[str, ...] = ("-m", "trainer.launcher"),
    ) -> None:
        self.data_dir = data_dir
        full_env = os.environ.copy()
        full_env["TRAINER_DATA_DIR"] = str(data_dir)
        full_env["PYTHONUNBUFFERED"] = "1"
        if env:
            full_env.update(env)
        for name in unset:
            full_env.pop(name, None)
        self.proc = subprocess.Popen(
            [sys.executable, *entry, "--no-window", "--port", "0", *extra_args],
            cwd=REPO,
            env=full_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.lines: queue.Queue[str] = queue.Queue()
        self.output: list[str] = []
        threading.Thread(target=self._pump, daemon=True).start()
        self.url = self._wait_url(timeout=120)
        self.base = self.url.split("/?t=")[0]
        self.port = int(self.base.rsplit(":", 1)[1])
        self.cookie = ""

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.output.append(line)
            self.lines.put(line)

    def _wait_url(self, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=0.5)
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise RuntimeError("launcher exited:\n" + "".join(self.output)) from None
                continue
            if line.startswith("TRAINER_URL "):
                return line.split(" ", 1)[1].strip()
        raise TimeoutError("launcher did not print its URL:\n" + "".join(self.output))

    def login(self) -> str:
        r = httpx.get(self.url, follow_redirects=False, timeout=10)
        assert r.status_code == 303, r.text
        self.cookie = r.cookies[COOKIE_NAME]
        return self.cookie

    @property
    def headers(self) -> dict[str, str]:
        return {"Origin": self.base, "Cookie": f"{COOKIE_NAME}={self.cookie}"}

    def http(self) -> httpx.Client:
        return httpx.Client(base_url=self.base, headers=self.headers, timeout=30)

    def ws_connect(self, **kwargs: Any) -> Any:
        from websockets.sync.client import connect

        return connect(
            f"ws://127.0.0.1:{self.port}/ws",
            additional_headers=self.headers,
            max_size=16 * 1024 * 1024,
            **kwargs,
        )

    def wait_exit(self, timeout: float) -> int | None:
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
