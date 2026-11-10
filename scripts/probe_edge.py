"""Stage 0.6 probe: Edge app window, microphone permission persistence, Playwright fake mic.

Usage: python scripts/probe_edge.py [app|playwright|all]

``app``: starts a tiny local page that asks for the microphone, opens it with
``msedge --app=... --user-data-dir=<temp>`` using Chromium's fake capture device (the real
microphone is never opened), and reports the permission state the page sees. The permission
prompt is answered through Windows UI Automation (scripts/uia_click.ps1). Then Edge is restarted
with the same profile and port, and with the same profile and another port.

``playwright``: launches Edge through Playwright (channel msedge, headless) with the fake-mic
flags from docs/testing.md and measures the signal level, with and without ``%noloop``.
"""

from __future__ import annotations

import http.server
import json
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>probe</title></head>
<body><p id="s">starting</p><script>
async function report(kind, value) {
  await fetch('/report?kind=' + kind + '&value=' + encodeURIComponent(value));
}
(async () => {
  const st = await navigator.permissions.query({name: 'microphone'});
  await report('initial', st.state);
  try {
    const s = await navigator.mediaDevices.getUserMedia({audio: true});
    await report('gum', 'ok');
    s.getTracks().forEach(t => t.stop());
  } catch (e) { await report('gum', 'error:' + e.name); }
  const st2 = await navigator.permissions.query({name: 'microphone'});
  await report('final', st2.state);
})();
</script></body></html>"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class ProbeServer:
    def __init__(self, port: int) -> None:
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        events = self.events

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path.startswith("/report"):
                    from urllib.parse import parse_qs, urlparse

                    q = parse_qs(urlparse(self.path).query)
                    events.put((q["kind"][0], q["value"][0]))
                    body = b"ok"
                    ctype = "text/plain"
                else:
                    body = PAGE.encode()
                    ctype = "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def wait(self, kind: str, timeout: float) -> str | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                k, v = self.events.get(timeout=0.2)
            except queue.Empty:
                continue
            if k == kind:
                return v
        return None

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def launch_app(port: int, profile: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            str(EDGE),
            f"--app=http://127.0.0.1:{port}/",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--use-fake-device-for-media-stream",
        ]
    )


def stop(proc: subprocess.Popen[bytes], profile: Path) -> None:
    # Edge may hand the window to a broker process: kill every process using this profile.
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '*{profile}*' }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
        ],
        check=False,
    )
    proc.wait(timeout=10)
    time.sleep(1.0)


def click_allow(timeout: float = 15.0) -> str:
    script = REPO / "scripts" / "uia_click.ps1"
    shot = Path(tempfile.gettempdir()) / "edge_app_probe.png"
    out = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-File",
            str(script),
            "-TimeoutSec",
            str(int(timeout)),
            "-Screenshot",
            str(shot),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return (out.stdout + out.stderr).strip()


def run_once(port: int, profile: Path, answer: bool) -> dict[str, Any]:
    srv = ProbeServer(port)
    proc = launch_app(port, profile)
    res: dict[str, Any] = {"port": port}
    try:
        res["initial"] = srv.wait("initial", 20)
        if answer and res["initial"] == "prompt":
            res["uia"] = click_allow()
        res["gum"] = srv.wait("gum", 20)
        res["final"] = srv.wait("final", 5)
    finally:
        stop(proc, profile)
        srv.close()
    return res


def probe_app() -> dict[str, Any]:
    profile = Path(tempfile.mkdtemp(prefix="edge-probe-"))
    port_a = free_port()
    out: dict[str, Any] = {"edge": str(EDGE)}
    try:
        out["first_launch"] = run_once(port_a, profile, answer=True)
        out["relaunch_same_port"] = run_once(port_a, profile, answer=False)
        port_b = free_port()
        out["relaunch_other_port"] = run_once(port_b, profile, answer=False)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    return out


PW_PAGE = """<!doctype html><html><body><script>
window.levels = [];
(async () => {
  const s = await navigator.mediaDevices.getUserMedia({audio: true});
  const ctx = new AudioContext();
  const src = ctx.createMediaStreamSource(s);
  const an = ctx.createAnalyser(); an.fftSize = 2048; src.connect(an);
  const buf = new Float32Array(an.fftSize);
  setInterval(() => {
    an.getFloatTimeDomainData(buf);
    let sum = 0; for (const v of buf) sum += v * v;
    window.levels.push(Math.sqrt(sum / buf.length));
  }, 50);
})();
</script></body></html>"""


def probe_playwright() -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    from tests.fixtures.make_fixtures import ensure_turns

    wav = ensure_turns()["turn_basic"]
    out: dict[str, Any] = {"wav": wav.name}
    port = free_port()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = PW_PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            for label, suffix in (("loop", ""), ("noloop", "%noloop")):
                for headless in (True, False):
                    profile = Path(tempfile.mkdtemp(prefix="pw-probe-"))
                    ctx = pw.chromium.launch_persistent_context(
                        str(profile),
                        channel="msedge",
                        headless=headless,
                        args=[
                            "--use-fake-device-for-media-stream",
                            "--use-fake-ui-for-media-stream",
                            f"--use-file-for-fake-audio-capture={wav}{suffix}",
                        ],
                    )
                    page = ctx.new_page()
                    page.goto(f"http://127.0.0.1:{port}/")
                    page.wait_for_timeout(15000)
                    levels: list[float] = page.evaluate("window.levels")
                    ctx.close()
                    shutil.rmtree(profile, ignore_errors=True)
                    loud = [i for i, v in enumerate(levels) if v > 0.02]
                    # turn_basic: ~3 s speech then 5 s silence -> 8 s period.
                    out[f"{label}_{'headless' if headless else 'headed'}"] = {
                        "samples": len(levels),
                        "max_level": round(max(levels), 4) if levels else None,
                        "loud_first_s": round(loud[0] * 0.05, 2) if loud else None,
                        "loud_last_s": round(loud[-1] * 0.05, 2) if loud else None,
                    }
    finally:
        httpd.shutdown()
    return out


def main(argv: list[str]) -> int:
    what = argv[1] if len(argv) > 1 else "all"
    result: dict[str, Any] = {}
    if what in ("app", "all"):
        result["app"] = probe_app()
    if what in ("playwright", "all"):
        result["playwright"] = probe_playwright()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
