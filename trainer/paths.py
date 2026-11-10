"""Filesystem locations of application data.

Everything the app writes lives under one per-user directory (``%LOCALAPPDATA%\\EnglishTrainer``
by default). Tests point ``TRAINER_DATA_DIR`` at a temporary directory and keep
``TRAINER_MODELS_DIR`` on the shared real models directory so models are not downloaded twice.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "EnglishTrainer"


def data_dir() -> Path:
    """Root of the application data directory (database, audio, logs, Edge profile)."""
    override = os.environ.get("TRAINER_DATA_DIR")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / APP_DIR_NAME


def models_dir() -> Path:
    """Directory with downloaded model weights; also used as ``HF_HOME``."""
    override = os.environ.get("TRAINER_MODELS_DIR")
    if override:
        return Path(override)
    return data_dir() / "models"


def configure_model_env() -> Path:
    """Point Hugging Face caches at the models directory before any model library is imported."""
    models = models_dir()
    models.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(models)
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    return models
