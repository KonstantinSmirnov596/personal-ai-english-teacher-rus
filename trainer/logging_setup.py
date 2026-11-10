"""Logging with key redaction.

Every record passes through :class:`RedactingFilter`, which removes registered secrets and
anything that looks like an API key or a bearer token, before it reaches any handler.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
import threading
from pathlib import Path

_secrets: set[str] = set()
_secrets_lock = threading.Lock()

# Patterns that look like keys: provider prefixes, bearer tokens, long opaque tokens.
_KEY_PATTERNS = [
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\b(?:sk|pk|rk|ak|key)[-_][A-Za-z0-9._-]{8,}"),
    re.compile(r"\b[A-Za-z0-9_-]{32,}\b"),
]
REDACTED = "[REDACTED]"


def register_secret(value: str) -> None:
    if value and len(value) >= 6:
        with _secrets_lock:
            _secrets.add(value)


def redact(text: str) -> str:
    with _secrets_lock:
        known = sorted(_secrets, key=len, reverse=True)
    for s in known:
        if s in text:
            text = text.replace(s, REDACTED)
    for pat in _KEY_PATTERNS:
        text = pat.sub(_replace_unless_uuid, text)
    return text


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _replace_unless_uuid(match: re.Match[str]) -> str:
    # Turn and session ids are UUIDs; they are not secrets and keep logs useful.
    return match.group(0) if _UUID.fullmatch(match.group(0)) else REDACTED


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        if record.exc_info:
            formatter = logging.Formatter()
            message = message + "\n" + formatter.formatException(record.exc_info)
            record.exc_info = None
            record.exc_text = None
        record.msg = redact(message)
        record.args = None
        return True


_configured = False


def setup_logging(log_dir: Path | None, level: int = logging.INFO) -> None:
    """Root logger -> rotating file (and stderr). Safe to call more than once."""
    global _configured
    root = logging.getLogger()
    if _configured:
        return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    redactor = RedactingFilter()
    handlers: list[logging.Handler] = []
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_dir / "trainer.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
            )
        )
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(redactor)
        root.addHandler(h)
    # Library loggers propagate to root; make sure records created elsewhere are redacted too.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    _configured = True
