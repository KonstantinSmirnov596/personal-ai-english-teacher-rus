"""Single running instance per data directory and activation of the existing window."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

WINDOW_TITLE = "Тренажёр"
ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9


def _mutex_name(data_dir: Path) -> str:
    digest = hashlib.sha1(str(data_dir.resolve()).lower().encode("utf-8")).hexdigest()[:16]
    return f"Local\\EnglishTrainer-{digest}"


def acquire(data_dir: Path) -> Any | None:
    """Named mutex held for the lifetime of the process; None when another instance has it."""
    if sys.platform != "win32":
        return object()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateMutexW(None, 0, _mutex_name(data_dir))
    if not handle:
        return object()  # cannot create a mutex: do not block the launch
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return handle


def wait_and_acquire(data_dir: Path, timeout_s: float) -> Any | None:
    deadline = time.monotonic() + timeout_s
    while True:
        lock = acquire(data_dir)
        if lock is not None or time.monotonic() >= deadline:
            return lock
        time.sleep(0.25)


def activate_window(title: str = WINDOW_TITLE) -> bool:
    """Bring the already open application window to the foreground."""
    if sys.platform != "win32":
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    found: list[int] = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

    def visit(hwnd: int, _lparam: int) -> int:
        if not user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
            return 1
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(ctypes.c_void_p(hwnd), buf, 256)
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(ctypes.c_void_p(hwnd), cls, 64)
        if buf.value == title and cls.value.startswith("Chrome_WidgetWin"):
            found.append(hwnd)
            return 0
        return 1

    user32.EnumWindows(proto(visit), 0)
    if not found:
        return False
    hwnd = ctypes.c_void_p(found[0])
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    return True


def write_info(data_dir: Path, port: int) -> None:
    """Diagnostics for the owner and scripts: which process serves this data directory."""
    data_dir.mkdir(parents=True, exist_ok=True)
    info = {"pid": os.getpid(), "port": port, "started_at": time.time()}
    (data_dir / "instance.json").write_text(json.dumps(info), encoding="utf-8")


def clear_info(data_dir: Path) -> None:
    path = data_dir / "instance.json"
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError):
        pass
