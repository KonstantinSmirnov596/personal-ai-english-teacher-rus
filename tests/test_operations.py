"""Stage 4 operations: single instance, friendly startup problems, log rotation, offline mode."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.launcher_proc import REPO, LauncherProcess
from trainer.logging_setup import RedactingFilter, rotating_handler


def run_second(data_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TRAINER_DATA_DIR"] = str(data_dir)
    return subprocess.run(
        [sys.executable, "-m", "trainer.launcher", "--no-window", "--port", "0", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_second_launch_does_not_start_a_second_core(tmp_path: Path) -> None:
    first = LauncherProcess(tmp_path, "--no-models")
    try:
        info = json.loads((tmp_path / "instance.json").read_text(encoding="utf-8"))
        # the venv python.exe is a trampoline: the core runs in its child process
        assert info["port"] == first.port and info["pid"] > 0
        started = time.monotonic()
        second = run_second(tmp_path, "--no-models", "--wait-existing", "1")
        assert second.returncode == 3
        assert "already running" in second.stdout
        assert "TRAINER_URL" not in second.stdout  # no second server
        assert time.monotonic() - started < 20
        assert first.proc.poll() is None
        again = json.loads((tmp_path / "instance.json").read_text(encoding="utf-8"))
        assert again == info
    finally:
        first.stop()
    # after the first core is gone the data directory can be served again
    third = LauncherProcess(tmp_path, "--no-models")
    try:
        assert third.port > 0
    finally:
        third.stop()


def test_no_gpu_is_reported_in_plain_words(tmp_path: Path) -> None:
    lp = LauncherProcess(tmp_path, env={"CUDA_VISIBLE_DEVICES": "-1"})
    try:
        lp.login()
        with lp.http() as http:
            deadline = time.monotonic() + 90
            health = http.get("/api/health").json()
            while health["status"] == "loading" and time.monotonic() < deadline:
                time.sleep(0.5)
                health = http.get("/api/health").json()
        assert health["status"] == "error"
        assert [p["code"] for p in health["problems"]] == ["no_gpu"]
        assert "NVIDIA" in health["problems"][0]["message_ru"]
        assert health["models"]["stt"] == "error"
    finally:
        lp.stop()


@pytest.mark.gpu
def test_missing_models_are_reported(tmp_path: Path) -> None:
    empty = tmp_path / "no-models"
    empty.mkdir()
    lp = LauncherProcess(tmp_path / "data", env={"TRAINER_MODELS_DIR": str(empty)})
    try:
        lp.login()
        with lp.http() as http:
            deadline = time.monotonic() + 90
            health = http.get("/api/health").json()
            while health["status"] == "loading" and time.monotonic() < deadline:
                time.sleep(0.5)
                health = http.get("/api/health").json()
        assert [p["code"] for p in health["problems"]] == ["models_missing"]
        assert "setup.ps1" in health["problems"][0]["message_ru"]
    finally:
        lp.stop()


def test_core_runs_with_hf_offline_and_logs_to_file(tmp_path: Path) -> None:
    lp = LauncherProcess(tmp_path, "--no-models", env={"HF_HUB_OFFLINE": ""})
    try:
        lp.login()
        with lp.http() as http:
            assert http.get("/api/health").status_code == 200
    finally:
        lp.stop()
    log = (tmp_path / "logs" / "trainer.log").read_text(encoding="utf-8")
    assert "core listening on 127.0.0.1" in log


def test_launcher_sets_offline_mode() -> None:
    code = (
        "import os, asyncio, argparse; os.environ.pop('HF_HUB_OFFLINE', None);"
        "import trainer.launcher as l, inspect;"
        "src = inspect.getsource(l.serve);"
        "assert 'HF_HUB_OFFLINE' in src; print('ok')"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True)
    assert out.stdout.strip() == "ok", out.stderr


def test_log_rotation_keeps_a_bounded_number_of_files(tmp_path: Path) -> None:
    handler = rotating_handler(tmp_path, max_bytes=2000, backups=3)
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger("rotation-test")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        for i in range(400):
            logger.info("line %d with a key sk-test-abcdefghijklmnop", i)
    finally:
        logger.removeHandler(handler)
        handler.close()
    files = sorted(p.name for p in tmp_path.glob("trainer.log*"))
    assert files == ["trainer.log", "trainer.log.1", "trainer.log.2", "trainer.log.3"]
    assert all(p.stat().st_size <= 2200 for p in tmp_path.glob("trainer.log*"))
    assert "sk-test-abcdefghijklmnop" not in (tmp_path / "trainer.log").read_text(encoding="utf-8")
