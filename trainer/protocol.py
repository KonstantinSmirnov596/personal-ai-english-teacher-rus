"""WebSocket protocol between the window and the core (docs/protocol.md)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PROTOCOL_VERSION = 1
MAX_TEXT_FRAME = 256 * 1024
MAX_BINARY_FRAME = 8 * 1024 * 1024
MIN_TURN_S = 0.3
MAX_TURN_S = 60.0

State = Literal["idle", "listening", "transcribing", "thinking", "speaking"]
ErrorCode = Literal[
    "no_active_connection",
    "llm_unauthorized",
    "llm_rate_limited",
    "llm_unreachable",
    "llm_bad_response",
    "stt_failed",
    "tts_failed",
    "audio_too_short",
    "audio_too_long",
    "bad_request",
    "internal",
]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AudioMeta(_In):
    sample_rate: int = Field(ge=16000, le=48000)
    format: Literal["pcm_s16le"]
    bytes: int = Field(gt=0, le=MAX_BINARY_FRAME)


class SessionStart(_In):
    type: Literal["session.start"]
    scenario_id: str = Field(min_length=1, max_length=100)
    level: Literal["A2", "B1", "B2", "C1"] | None = None


class SessionEnd(_In):
    type: Literal["session.end"]


class TurnBegin(_In):
    type: Literal["turn.begin"]
    turn_id: str = Field(min_length=1, max_length=64)
    mode: Literal["manual", "auto"]


class TurnAudio(_In):
    type: Literal["turn.audio"]
    turn_id: str = Field(min_length=1, max_length=64)
    audio: AudioMeta


class TurnCancel(_In):
    type: Literal["turn.cancel"]
    turn_id: str = Field(min_length=1, max_length=64)


class TurnRetry(_In):
    type: Literal["turn.retry"]
    turn_id: str = Field(min_length=1, max_length=64)


class Interrupt(_In):
    type: Literal["interrupt"]


class PlaybackStarted(_In):
    type: Literal["playback.started"]
    turn_id: str
    client_first_audio_ms: float | None = None


class PlaybackEnded(_In):
    type: Literal["playback.ended"]
    turn_id: str
    interrupted: bool = False


class HelperHint(_In):
    type: Literal["helper.hint"]
    request_id: str = Field(min_length=1, max_length=64)


class HelperTranslate(_In):
    type: Literal["helper.translate"]
    request_id: str = Field(min_length=1, max_length=64)
    turn_id: str


class HelperHowToSay(_In):
    type: Literal["helper.how_to_say"]
    request_id: str = Field(min_length=1, max_length=64)
    text_ru: str = Field(min_length=1, max_length=500)


class TutorReplay(_In):
    type: Literal["tutor.replay"]
    turn_id: str
    speed: Literal[1.0, 0.8] = 1.0


class ExerciseAnswer(_In):
    type: Literal["exercise.answer"]
    request_id: str = Field(min_length=1, max_length=64)
    audio: AudioMeta


Incoming = Annotated[
    SessionStart
    | SessionEnd
    | TurnBegin
    | TurnAudio
    | TurnCancel
    | TurnRetry
    | Interrupt
    | PlaybackStarted
    | PlaybackEnded
    | HelperHint
    | HelperTranslate
    | HelperHowToSay
    | TutorReplay
    | ExerciseAnswer,
    Field(discriminator="type"),
]
incoming_adapter: TypeAdapter[Any] = TypeAdapter(Incoming)


def parse_incoming(raw: str | bytes) -> Any:
    return incoming_adapter.validate_json(raw)


# --- core -> window ---------------------------------------------------------------------------


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectionRef(_Out):
    id: int
    name: str
    model: str


class Hello(_Out):
    type: Literal["hello"] = "hello"
    protocol: int = PROTOCOL_VERSION
    app_version: str
    state: State
    active_connection: ConnectionRef | None
    settings: dict[str, Any]
    models_ready: bool


class ScenarioRef(_Out):
    id: str
    title_ru: str


class SessionStarted(_Out):
    type: Literal["session.started"] = "session.started"
    session_id: str
    scenario: ScenarioRef
    connection: ConnectionRef


class StateMsg(_Out):
    type: Literal["state"] = "state"
    value: State
    turn_id: str | None = None


class WordOut(_Out):
    w: str
    start: float
    end: float
    p: float


class UserTranscript(_Out):
    type: Literal["user.transcript"] = "user.transcript"
    turn_id: str
    text: str
    words: list[WordOut]
    low_conf: list[int]


class TutorDelta(_Out):
    type: Literal["tutor.delta"] = "tutor.delta"
    turn_id: str
    text: str


class AudioOut(_Out):
    sample_rate: int
    format: Literal["pcm_s16le"] = "pcm_s16le"
    bytes: int


class TutorSentence(_Out):
    type: Literal["tutor.sentence"] = "tutor.sentence"
    turn_id: str
    index: int
    text: str
    replay: bool = False
    audio: AudioOut


class TutorDone(_Out):
    type: Literal["tutor.done"] = "tutor.done"
    turn_id: str
    text: str
    interrupted: bool


class CorrectionItem(_Out):
    id: str
    quote: str
    start: int | None
    end: int | None
    fix: str
    type: Literal["grammar", "vocabulary", "phrasing"]
    note_ru: str
    pattern: str


class Corrections(_Out):
    type: Literal["corrections"] = "corrections"
    turn_id: str
    status: Literal["ok", "empty", "failed", "disabled"]
    items: list[CorrectionItem] = Field(default_factory=list)
    better: str = ""


class PronFlag(_Out):
    word_index: int
    expected: str
    heard: str
    tip_ru: str


class PronFlags(_Out):
    type: Literal["pron.flags"] = "pron.flags"
    turn_id: str
    items: list[PronFlag]


class HelperResult(_Out):
    type: Literal["helper.result"] = "helper.result"
    request_id: str
    kind: Literal["hint", "translate", "how_to_say", "exercise"]
    data: dict[str, Any]


class SummaryMsg(_Out):
    type: Literal["summary"] = "summary"
    session_id: str
    key_takeaway: dict[str, Any] | None
    exercise: dict[str, Any] | None
    corrections: list[dict[str, Any]]
    patterns: list[dict[str, Any]]
    sounds: list[dict[str, Any]]
    phrases: list[dict[str, Any]]


class Timing(_Out):
    type: Literal["timing"] = "timing"
    turn_id: str
    stt_ms: float | None
    llm_first_sentence_ms: float | None
    tts_first_ms: float | None
    first_audio_ms: float | None


class ErrorMsg(_Out):
    type: Literal["error"] = "error"
    code: ErrorCode
    message_ru: str
    retryable: bool
    turn_id: str | None = None
    request_id: str | None = None
