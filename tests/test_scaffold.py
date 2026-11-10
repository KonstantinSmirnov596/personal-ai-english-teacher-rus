"""Smoke checks of the project skeleton."""

from __future__ import annotations

import os
from pathlib import Path

import trainer
from trainer import paths


def test_package_imports() -> None:
    assert trainer.__version__


def test_data_dir_honours_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TRAINER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("TRAINER_MODELS_DIR", raising=False)
    assert paths.data_dir() == tmp_path / "data"
    assert paths.models_dir() == tmp_path / "data" / "models"


def test_models_dir_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TRAINER_MODELS_DIR", str(tmp_path / "m"))
    assert paths.models_dir() == tmp_path / "m"


def test_default_data_dir_is_localappdata(monkeypatch) -> None:
    monkeypatch.delenv("TRAINER_DATA_DIR", raising=False)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        assert paths.data_dir() == Path(local) / "EnglishTrainer"
