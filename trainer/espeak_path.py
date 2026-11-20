"""espeak-ng data path for app folders with Cyrillic or other non-ASCII letters (installer).

espeak-ng opens its data with the narrow (ANSI) file API, so a path such as
``C:\\Users\\Иван\\Тренажёр\\.venv\\...\\espeak-ng-data`` fails and espeak-ng falls
back to a build-time path that does not exist. phonemizer cannot be given the Windows 8.3 short path
either: it calls ``Path.resolve()``, which expands the short name back to the long one.

When the data path is not ASCII, this module points espeak-ng's own ``ESPEAK_DATA_PATH``
variable to the short path and makes ``espeakng_loader.get_data_path()`` return ``None``, so
phonemizer (ours and misaki's, used by Kokoro) passes no path and espeak-ng reads the variable.
The installer refuses folders without an ASCII short name (``scripts/installer.ps1``).
"""

from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)
_done = False


def short_path(path: str) -> str:
    """Windows 8.3 short form of an existing path ('' when the volume keeps none)."""
    try:
        from ctypes import wintypes

        fn = ctypes.windll.kernel32.GetShortPathNameW  # type: ignore[attr-defined]
        fn.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        buf = ctypes.create_unicode_buffer(1024)
        return buf.value if fn(path, buf, 1024) else ""
    except (AttributeError, OSError):
        return ""


def configure() -> None:
    """Idempotent; call before anything creates an espeak backend."""
    global _done
    if _done:
        return
    _done = True
    import espeakng_loader

    data = str(espeakng_loader.get_data_path())
    if data.isascii():
        return
    short = short_path(data)
    if not short or not short.isascii() or not Path(short).is_dir():
        log.warning("espeak-ng data path has non-ASCII letters and no short name")
        return
    os.environ["ESPEAK_DATA_PATH"] = short
    espeakng_loader.get_data_path = lambda: None  # type: ignore[assignment]
    log.info("espeak-ng data path: using the short name of the app folder")
