"""Application launcher: starts the core on 127.0.0.1 and opens the Edge app window.

    python -m trainer.launcher [--no-window] [--port N] [--exit-when-idle S]

``--no-window`` prints the start URL (with the one-time token) to stdout instead of opening Edge;
tests use it to drive their own browser. Protection is identical in both modes.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_PORTS = tuple(range(47613, 47623))
IDLE_EXIT_S = 10.0

log = logging.getLogger("trainer.launcher")


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def pick_port(requested: int | None) -> int:
    if requested == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])
    if requested is not None:
        return requested
    for port in DEFAULT_PORTS:
        if port_free(port):
            return port
    raise SystemExit("no free port in the default range")


def find_edge() -> Path | None:
    candidates: list[Path] = []
    try:
        import winreg

        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(
                    hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
                ) as key:
                    candidates.append(Path(winreg.QueryValue(key, None)))
            except OSError:
                continue
    except ImportError:
        pass
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            candidates.append(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    for c in candidates:
        if c.is_file():
            return c
    return None


def edge_args(edge: Path, url: str, profile: Path) -> list[str]:
    return [
        str(edge),
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1180,880",
    ]


def open_window(url: str, profile: Path) -> subprocess.Popen[bytes] | None:
    edge = find_edge()
    if edge is None:
        log.error("Microsoft Edge not found")
        return None
    profile.mkdir(parents=True, exist_ok=True)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        edge_args(edge, url, profile),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


async def serve(args: argparse.Namespace) -> int:
    import uvicorn

    from trainer import instance
    from trainer.core import Core
    from trainer.paths import configure_model_env, data_dir
    from trainer.protocol import MAX_BINARY_FRAME
    from trainer.server.app import create_app
    from trainer.server.guard import AccessKeys

    configure_model_env()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    root = data_dir()
    port = pick_port(args.port)
    keys = AccessKeys(port=port)
    core = Core(data_dir=root, keys=keys, load_models=not args.no_models)
    app = create_app(core)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_config=None,
        access_log=False,
        server_header=False,
        date_header=False,
        ws_max_size=MAX_BINARY_FRAME + 64 * 1024,
        ws_ping_interval=20.0,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    url = f"http://127.0.0.1:{port}/?t={keys.launch_token}"
    idle_exit = args.exit_when_idle
    if idle_exit is None:
        idle_exit = 0.0 if args.no_window else IDLE_EXIT_S

    async def after_start() -> None:
        while not server.started:
            if server.should_exit:
                return
            await asyncio.sleep(0.05)
        log.info("core listening on 127.0.0.1:%d", port)
        instance.write_info(root, port)
        if args.no_window:
            print(f"TRAINER_URL {url}", flush=True)
        else:
            open_window(url, root / "edge-profile")
        while not server.should_exit:
            await asyncio.sleep(0.5)
            if (
                idle_exit > 0
                and core.ws_ever_connected
                and core.ws_count == 0
                and time.monotonic() - core.ws_last_change > idle_exit
            ):
                log.info("window closed and not reconnected for %.0f s: exiting", idle_exit)
                server.should_exit = True

    watcher = asyncio.create_task(after_start())
    try:
        await server.serve()
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        instance.clear_info(root)
    return 0


def main(argv: list[str] | None = None) -> int:
    from trainer.logging_setup import setup_logging
    from trainer.paths import data_dir

    ap = argparse.ArgumentParser(prog="trainer")
    ap.add_argument(
        "--no-window", action="store_true", help="print the URL instead of opening Edge"
    )
    ap.add_argument("--port", type=int, default=None, help="port (0 = any free port)")
    ap.add_argument("--exit-when-idle", type=float, default=None, help="seconds; 0 disables")
    ap.add_argument("--no-models", action="store_true", help="do not preload models at start")
    ap.add_argument(
        "--wait-existing",
        type=float,
        default=15.0,
        help="seconds to wait for a closing instance before giving up",
    )
    args = ap.parse_args(argv)

    # Started from the shortcut (pythonw) there is no console: give libraries a place to print.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

    from trainer import instance

    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    lock = instance.acquire(root)
    if lock is None:
        # Another core serves this data directory: show its window instead of starting twice.
        if instance.activate_window():
            return 0
        # No window: that core is in its 10 s shutdown grace period. Wait for it to exit.
        lock = instance.wait_and_acquire(root, args.wait_existing)
        if lock is None:
            print("trainer is already running", flush=True)
            return 3
    setup_logging(root / "logs")
    try:
        return asyncio.run(serve(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
