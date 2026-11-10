"""Shared pytest setup: isolate tests from the owner's data and keys."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from trainer import paths

# Tests share the real models directory (no re-downloads) but never touch the owner's data dir.
_REAL_MODELS = paths.models_dir()
os.environ.setdefault("TRAINER_MODELS_DIR", str(_REAL_MODELS))
os.environ["TRAINER_DATA_DIR"] = str(Path(tempfile.mkdtemp(prefix="trainer-test-data-")))
os.environ["TRAINER_KEYRING_SERVICE"] = "EnglishTrainer-test"
os.environ.setdefault("HF_HUB_OFFLINE", "1")
