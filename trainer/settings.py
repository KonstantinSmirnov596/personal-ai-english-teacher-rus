"""User settings: one object, stored key by key in the ``setting`` table."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trainer.model_registry import KOKORO_VOICES

CorrectionMode = Literal["instant", "on_click", "end", "off"]
Level = Literal["A2", "B1", "B2", "C1"]
TurnMode = Literal["manual", "auto"]


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_mode: TurnMode = "manual"
    pause_ms: int = Field(default=1500, ge=1000, le=3000)
    correction_mode: CorrectionMode = "on_click"
    voice: str = "af_heart"
    speed: float = Field(default=1.0, ge=0.7, le=1.3)
    level: Level = "B1"
    recast: bool = True
    headphones: bool = False
    audio_retention_days: int = Field(default=30, ge=0, le=3650)
    low_conf_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    input_device_id: str = ""
    output_device_id: str = ""
    noise_suppression: bool = True
    # Stage 3: cautious pronunciation marks in free conversation.
    pron_in_conversation: bool = True
    # Reading mode: open the microphone without noise suppression and auto gain.
    reading_raw_mic: bool = False

    @field_validator("voice")
    @classmethod
    def _voice_known(cls, v: str) -> str:
        if v not in KOKORO_VOICES:
            raise ValueError("unknown voice")
        return v


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_mode: TurnMode | None = None
    pause_ms: int | None = Field(default=None, ge=1000, le=3000)
    correction_mode: CorrectionMode | None = None
    voice: str | None = None
    speed: float | None = Field(default=None, ge=0.7, le=1.3)
    level: Level | None = None
    recast: bool | None = None
    headphones: bool | None = None
    audio_retention_days: int | None = Field(default=None, ge=0, le=3650)
    low_conf_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    input_device_id: str | None = None
    output_device_id: str | None = None
    noise_suppression: bool | None = None
    pron_in_conversation: bool | None = None
    reading_raw_mic: bool | None = None
