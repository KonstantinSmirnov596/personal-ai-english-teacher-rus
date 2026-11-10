"""Shared pytest setup: isolate tests from the owner's data and keys."""

from __future__ import annotations

import contextlib
import os
import secrets
import tempfile
from collections.abc import Iterator
from pathlib import Path

import keyring
import keyring.errors
import pytest

from trainer import paths

# Tests share the real models directory (no re-downloads) but never touch the owner's data dir.
_REAL_MODELS = paths.models_dir()
os.environ.setdefault("TRAINER_MODELS_DIR", str(_REAL_MODELS))
os.environ["TRAINER_DATA_DIR"] = str(Path(tempfile.mkdtemp(prefix="trainer-test-data-")))
os.environ["TRAINER_KEYRING_SERVICE"] = "EnglishTrainer-test"
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from tests.mock_llm import MockLLM  # noqa: E402  (after the environment is isolated)


@pytest.fixture
def test_key() -> str:
    return "sk-test-" + secrets.token_urlsafe(24)


@pytest.fixture
def mock_llm(test_key: str) -> Iterator[MockLLM]:
    mock = MockLLM(test_key).start()
    try:
        yield mock
    finally:
        mock.stop()


@pytest.fixture(autouse=True)
def _clean_test_keyring(request: pytest.FixtureRequest) -> Iterator[None]:
    """Remove every key the test stored in the EnglishTrainer-test service.

    GPU and e2e modules share one running core per module and clean up their own keys.
    """
    yield
    if request.node.get_closest_marker("gpu") or request.node.get_closest_marker("e2e"):
        return
    service = os.environ["TRAINER_KEYRING_SERVICE"]
    assert service == "EnglishTrainer-test"
    for i in range(1, 11):
        with contextlib.suppress(keyring.errors.PasswordDeleteError):
            keyring.delete_password(service, f"connection-{i}")
