"""Stage 4: time from launch to a usable application with a warm disk cache."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.launcher_proc import REPO, LauncherProcess

pytestmark = pytest.mark.gpu

GOAL_S = 20.0


def test_startup_time(tmp_path: Path) -> None:
    runs = []
    for i in range(2):
        started = time.perf_counter()
        lp = LauncherProcess(tmp_path / f"run{i}")
        try:
            server_s = time.perf_counter() - started
            lp.login()
            with lp.http() as http:
                assert http.get("/").status_code == 200  # the window can render now
                window_s = time.perf_counter() - started
                while http.get("/api/health").json()["status"] != "ok":
                    assert time.perf_counter() - started < 120
                    time.sleep(0.1)
                ready_s = time.perf_counter() - started
        finally:
            lp.stop()
        runs.append(
            {
                "server_s": round(server_s, 2),
                "window_s": round(window_s, 2),
                "models_ready_s": round(ready_s, 2),
            }
        )
    out = REPO / "docs" / "reports" / "data" / "startup.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"goal_s": GOAL_S, "runs": runs}, indent=2), encoding="utf-8")
    best = min(r["models_ready_s"] for r in runs)
    # The 20 s goal is reported, not enforced (docs/PLAN.md); this guards against regressions.
    assert best < 30.0, runs
