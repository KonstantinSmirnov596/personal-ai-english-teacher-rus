"""API keys in Windows Credential Manager through ``keyring``.

Keys never touch the database, files or logs. Every key the process sees is registered with the
log redaction filter so that even an accidental log line cannot carry it.
"""

from __future__ import annotations

import contextlib
import os

import keyring
import keyring.errors

from trainer.logging_setup import register_secret

DEFAULT_SERVICE = "EnglishTrainer"


def service_name() -> str:
    return os.environ.get("TRAINER_KEYRING_SERVICE") or DEFAULT_SERVICE


def _user(conn_id: int) -> str:
    return f"connection-{conn_id}"


def last4(key: str) -> str:
    return key[-4:] if len(key) >= 4 else key


def save_key(conn_id: int, key: str) -> str:
    register_secret(key)
    keyring.set_password(service_name(), _user(conn_id), key)
    return last4(key)


def load_key(conn_id: int) -> str | None:
    key = keyring.get_password(service_name(), _user(conn_id))
    if key:
        register_secret(key)
    return key


def delete_key(conn_id: int) -> None:
    with contextlib.suppress(keyring.errors.PasswordDeleteError):
        keyring.delete_password(service_name(), _user(conn_id))


def delete_all(conn_ids: list[int]) -> None:
    for cid in conn_ids:
        delete_key(cid)
