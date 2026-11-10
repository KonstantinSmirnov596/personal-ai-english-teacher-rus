"""Pinned model repositories and local resolution.

Models are downloaded once by ``scripts/setup.ps1`` (``python -m trainer.setup_models``) into the
models directory and are then resolved strictly offline from the pinned commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from trainer.paths import configure_model_env

# American English Kokoro voices (lang_code 'a'), from hexgrad/Kokoro-82M VOICES.md.
KOKORO_VOICES: tuple[str, ...] = (
    "af_heart",
    "af_bella",
    "af_nicole",
    "af_aoede",
    "af_kore",
    "af_sarah",
    "af_alloy",
    "af_nova",
    "af_sky",
    "af_jessica",
    "af_river",
    "am_fenrir",
    "am_michael",
    "am_puck",
    "am_echo",
    "am_eric",
    "am_liam",
    "am_onyx",
    "am_santa",
    "am_adam",
)


@dataclass(frozen=True)
class ModelRepo:
    key: str
    repo_id: str
    revision: str
    allow_patterns: tuple[str, ...]
    license: str


STT = ModelRepo(
    key="stt",
    # faster-whisper maps the "turbo" alias to this repository (faster_whisper/utils.py).
    repo_id="mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    revision="0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
    allow_patterns=(
        "config.json",
        "model.bin",
        "preprocessor_config.json",
        "tokenizer.json",
        "vocabulary.json",
    ),
    license="mit",
)

TTS = ModelRepo(
    key="tts",
    repo_id="hexgrad/Kokoro-82M",
    revision="f3ff3571791e39611d31c381e3a41a3af07b4987",
    allow_patterns=("config.json", "kokoro-v1_0.pth", *(f"voices/{v}.pt" for v in KOKORO_VOICES)),
    license="apache-2.0",
)

PHONEME = ModelRepo(
    key="phoneme",
    repo_id="facebook/wav2vec2-lv-60-espeak-cv-ft",
    revision="ae45363bf3413b374fecd9dc8bc1df0e24c3b7f4",
    allow_patterns=(
        "config.json",
        "preprocessor_config.json",
        "pytorch_model.bin",
        "special_tokens_map.json",
        "tokenizer_config.json",
        "vocab.json",
    ),
    license="apache-2.0",
)

ALL_MODELS: tuple[ModelRepo, ...] = (STT, TTS, PHONEME)


def local_path(model: ModelRepo) -> Path:
    """Path of the pinned snapshot in the models directory; raises if it was never downloaded."""
    configure_model_env()
    from huggingface_hub import snapshot_download

    path = Path(
        snapshot_download(
            repo_id=model.repo_id,
            revision=model.revision,
            allow_patterns=list(model.allow_patterns),
            local_files_only=True,
        )
    )
    missing = [f for f in model.allow_patterns if not (path / f).is_file()]
    if missing:
        raise FileNotFoundError(f"{model.repo_id}: missing files {missing[:3]}")
    return path


def download(model: ModelRepo) -> Path:
    configure_model_env()
    from huggingface_hub import snapshot_download

    return Path(
        snapshot_download(
            repo_id=model.repo_id,
            revision=model.revision,
            allow_patterns=list(model.allow_patterns),
        )
    )
