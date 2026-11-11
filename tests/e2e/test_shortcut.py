"""Stage 4.2: the shortcut starts the app without a console, a second launch does not start a
second core, and the core exits after the window is closed.

Opens a real Edge app window for about half a minute (temporary data directory and profile).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from tests.launcher_proc import REPO

pytestmark = pytest.mark.e2e


def ps(command: str, env: dict[str, str] | None = None) -> str:
    out = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
        check=False,
    )
    return out.stdout.strip()


def edge_windows(profile: Path) -> list[int]:
    """PIDs of top-level Edge processes started with this profile in app mode."""
    out = ps(
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{profile}*' -and $_.CommandLine -like '*--app=*'"
        " -and $_.CommandLine -notlike '*--type=*' } | ForEach-Object { $_.ProcessId }"
    )
    return [int(x) for x in out.split()]


def kill_edge(profile: Path) -> None:
    ps(
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{profile}*' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )


def wait_for(predicate, timeout: float, what: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.3)
    raise TimeoutError(what)


def test_shortcut_starts_app_once_and_core_exits_with_window(tmp_path: Path) -> None:
    out_dir = tmp_path / "lnk"
    data = tmp_path / "data"
    profile = data / "edge-profile"
    made = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-File",
            str(REPO / "scripts" / "make_shortcut.ps1"),
            "-OutDir",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert made.returncode == 0, made.stderr
    links = list(out_dir.glob("*.lnk"))
    assert len(links) == 1 and links[0].stem == "Тренажёр"
    props = ps(
        f"$l = (New-Object -ComObject WScript.Shell).CreateShortcut('{links[0]}');"
        " $l.TargetPath; $l.Arguments; $l.WorkingDirectory; $l.IconLocation"
    ).splitlines()
    assert props[0].lower().endswith("pythonw.exe") and Path(props[0]).exists()
    assert props[1] == "-m trainer.launcher"
    assert Path(props[2]) == REPO
    assert "trainer.ico" in props[3]

    env = os.environ.copy()
    env["TRAINER_DATA_DIR"] = str(data)
    info_file = data / "instance.json"
    try:
        ps(f"Start-Process -FilePath '{links[0]}'", env=env)
        wait_for(info_file.exists, 40, "the core did not start from the shortcut")
        info = json.loads(info_file.read_text(encoding="utf-8"))
        # no console: the core runs under pythonw
        assert ps(f"(Get-Process -Id {info['pid']}).ProcessName").lower() == "pythonw"
        assert (
            httpx.get(f"http://127.0.0.1:{info['port']}/api/health", timeout=5).status_code == 401
        )
        wait_for(lambda: edge_windows(profile), 30, "the Edge app window did not open")
        time.sleep(3.0)  # let the window connect its WebSocket

        # second launch: no second core, no second window
        ps(f"Start-Process -FilePath '{links[0]}'", env=env)
        time.sleep(5.0)
        assert json.loads(info_file.read_text(encoding="utf-8")) == info
        assert len(edge_windows(profile)) == 1

        # closing the window ends the core by itself (10 s grace period)
        kill_edge(profile)
        wait_for(lambda: not info_file.exists(), 40, "the core did not exit after the window")
        wait_for(
            lambda: ps(f"Get-Process -Id {info['pid']} -ErrorAction SilentlyContinue") == "",
            30,
            "the core process is still running",
        )
    finally:
        kill_edge(profile)
        if info_file.exists():
            pid = json.loads(info_file.read_text(encoding="utf-8"))["pid"]
            ps(f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue")
