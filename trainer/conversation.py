"""Conversation orchestrator for one window: turn state machine and reply pipeline.

States: idle -> listening -> transcribing -> thinking -> speaking -> idle (docs/protocol.md).
A learner turn is transcribed, then two independent LLM requests start: the tutor reply
(streamed, split into sentences, each synthesized and sent as soon as it is ready) and the
corrector (JSON, text only). ``interrupt`` cancels the reply but never the corrector.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from trainer.core import Core, NoActiveConnection
from trainer.corrector import run_corrector
from trainer.i18n_ru import error_text
from trainer.llm.client import LLMError
from trainer.prompts import tutor as tutor_prompt
from trainer.protocol import (
    MAX_TURN_S,
    MIN_TURN_S,
    AudioOut,
    ConnectionRef,
    CorrectionItem,
    Corrections,
    ErrorMsg,
    Hello,
    PlaybackEnded,
    PlaybackStarted,
    ScenarioRef,
    SessionEnd,
    SessionStart,
    SessionStarted,
    StateMsg,
    SummaryMsg,
    Timing,
    TurnAudio,
    TurnBegin,
    TurnCancel,
    TurnRetry,
    TutorDelta,
    TutorDone,
    TutorSentence,
    UserTranscript,
    WordOut,
)
from trainer.speech.audio import (
    STT_RATE,
    TTS_RATE,
    float_to_pcm16,
    pcm16_to_float,
    resample,
    write_wav,
)
from trainer.speech.sentences import (
    SentenceSplitter,
    clean_for_display,
    clean_for_speech,
    has_cyrillic,
)
from trainer.speech.tts import CyrillicTextError
from trainer.storage.repo import new_id

log = logging.getLogger(__name__)

CORRECTOR_WAIT_ON_END_S = 15.0


class SenderLike(Protocol):
    closed: bool

    async def send(self, msg: Any, audio: bytes | None = None) -> None: ...


@dataclass
class SessionCtx:
    id: str
    scenario: dict[str, Any]
    level: str
    correction_mode: str
    system_prompt: str
    history: list[dict[str, str]] = field(default_factory=list)
    history_summary: str = ""
    last_tutor_text: str = ""
    last_user_turn_id: str | None = None
    retry_pending: bool = False


@dataclass
class ReplyCtx:
    turn_id: str
    user_turn_id: str | None
    started: float
    sentences_sent: list[str] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)
    finished: bool = False
    timing: dict[str, float | None] = field(default_factory=dict)


class Conversation:
    def __init__(self, core: Core, sender: SenderLike) -> None:
        self.core = core
        self.sender = sender
        self.state = "idle"
        self.session: SessionCtx | None = None
        self.reply: ReplyCtx | None = None
        self.reply_task: asyncio.Task[None] | None = None
        self.turn_task: asyncio.Task[None] | None = None
        self.background: set[asyncio.Task[Any]] = set()
        self.corrector_tasks: set[asyncio.Task[Any]] = set()
        self._fold_lock = asyncio.Lock()
        self.replay_cache: dict[Any, list[tuple[str, bytes]]] = {}
        self.last_summary: dict[str, Any] | None = None

    # --- plumbing --------------------------------------------------------------------------

    async def send(self, msg: Any, audio: bytes | None = None) -> None:
        await self.sender.send(msg, audio)

    async def set_state(self, value: str, turn_id: str | None = None) -> None:
        if value == self.state:
            return
        self.state = value
        await self.send(StateMsg(value=value, turn_id=turn_id))  # type: ignore[arg-type]

    async def error(
        self,
        code: str,
        *,
        turn_id: str | None = None,
        request_id: str | None = None,
        retryable: bool = False,
        detail: str = "",
    ) -> None:
        message = error_text(code)
        if detail:
            message = f"{message} ({detail})"[:300]
        await self.send(
            ErrorMsg(
                code=code,  # type: ignore[arg-type]
                message_ru=message,
                retryable=retryable,
                turn_id=turn_id,
                request_id=request_id,
            )
        )

    def _spawn(self, coro: Any, bucket: set[asyncio.Task[Any]] | None = None) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        target = self.background if bucket is None else bucket
        target.add(task)
        task.add_done_callback(target.discard)
        return task

    async def send_hello(self) -> None:
        active = self.core.connections.active()
        await self.send(
            Hello(
                app_version=self.core.app_version,
                state=self.state,  # type: ignore[arg-type]
                active_connection=ConnectionRef(**self.core.connection_ref(active))
                if active
                else None,
                settings=self.core.settings.get().model_dump(),
                models_ready=self.core.models.ready,
            )
        )

    async def close(self) -> None:
        for task in [self.reply_task, self.turn_task, *self.background]:
            if task is not None and not task.done():
                task.cancel()
        # Correctors are allowed to finish so their results are stored.
        if self.corrector_tasks:
            await asyncio.wait(list(self.corrector_tasks), timeout=CORRECTOR_WAIT_ON_END_S)

    # --- dispatch --------------------------------------------------------------------------

    async def handle(self, msg: Any, audio: bytes | None) -> None:
        if isinstance(msg, SessionStart):
            await self.start_session(msg)
        elif isinstance(msg, SessionEnd):
            await self.end_session()
        elif isinstance(msg, TurnBegin):
            if self.state in ("thinking", "speaking"):
                await self.interrupt()
            if self.session is None:
                await self.error("bad_request", turn_id=msg.turn_id)
                return
            await self.set_state("listening", msg.turn_id)
        elif isinstance(msg, TurnAudio):
            assert audio is not None
            if self.state in ("thinking", "speaking"):
                await self.interrupt()
            self.turn_task = asyncio.create_task(
                self.process_turn(msg.turn_id, audio, msg.audio.sample_rate, time.perf_counter())
            )
        elif isinstance(msg, TurnCancel):
            if self.state == "listening":
                await self.set_state("idle")
        elif isinstance(msg, TurnRetry):
            await self.retry(msg.turn_id)
        elif type(msg).__name__ == "Interrupt":
            await self.interrupt()
        elif isinstance(msg, PlaybackStarted):
            self._record_playback(msg)
        elif isinstance(msg, PlaybackEnded):
            if self.state == "speaking" and (self.reply is None or self.reply.finished):
                await self.set_state("idle")
        else:
            await self.handle_extra(msg, audio)

    async def handle_extra(self, msg: Any, audio: bytes | None) -> None:
        """Helpers and exercise answers (stage 2)."""
        from trainer.helpers import handle_helper

        await handle_helper(self, msg, audio)

    # --- session ---------------------------------------------------------------------------

    async def start_session(self, msg: SessionStart) -> None:
        if self.session is not None:
            await self.end_session(send_summary=False)
        scenario_row = self.core.scenarios.get(msg.scenario_id)
        if scenario_row is None:
            await self.error("bad_request")
            return
        try:
            conn_row, _ep = self.core.active_endpoint()
        except NoActiveConnection:
            await self.error("no_active_connection")
            return
        except LLMError as e:
            await self.error(e.code, retryable=e.retryable)
            return
        settings = self.core.settings.get()
        scenario = self.core.scenarios.to_api(scenario_row)
        level = msg.level or settings.level
        session_id = self.core.sessions.create(
            scenario_id=scenario["id"],
            scenario_title_ru=scenario["title_ru"],
            level=level,
            correction_mode=settings.correction_mode,
            connection_id=int(conn_row["id"]),
            connection_name=conn_row["name"],
            model=conn_row["model"],
        )
        system_prompt = tutor_prompt.build_system_prompt(
            level=level,
            scenario=scenario,
            recast=settings.recast,
            focus_lines=self.focus_lines(),
        )
        self.session = SessionCtx(
            id=session_id,
            scenario=scenario,
            level=level,
            correction_mode=settings.correction_mode,
            system_prompt=system_prompt,
        )
        await self.send(
            SessionStarted(
                session_id=session_id,
                scenario=ScenarioRef(id=scenario["id"], title_ru=scenario["title_ru"]),
                connection=ConnectionRef(**self.core.connection_ref(conn_row)),
            )
        )
        openers = scenario.get("openers") or ["Hi there. What would you like to talk about today?"]
        opener = random.choice(openers)
        self.reply_task = asyncio.create_task(self.speak_fixed(opener))

    def focus_lines(self) -> list[str]:
        """Practice focus from recurring mistakes (stage 2.5)."""
        from trainer.patterns import focus_lines

        return focus_lines(self.core)

    async def end_session(self, send_summary: bool = True) -> None:
        session = self.session
        if session is None:
            return
        await self.interrupt(record=True)
        if self.corrector_tasks:
            await asyncio.wait(list(self.corrector_tasks), timeout=CORRECTOR_WAIT_ON_END_S)
        self.session = None
        from trainer.summary import build_summary

        summary = await build_summary(self.core, session) if send_summary else None
        self.core.sessions.end(session.id, summary)
        self.last_summary = summary
        self.replay_cache.clear()
        if summary is not None:
            await self.send(SummaryMsg(session_id=session.id, **summary))
        await self.set_state("idle")

    # --- turn ------------------------------------------------------------------------------

    async def process_turn(self, turn_id: str, pcm: bytes, rate: int, received: float) -> None:
        session = self.session
        if session is None:
            await self.error("bad_request", turn_id=turn_id)
            return
        audio = pcm16_to_float(pcm)
        duration = len(audio) / rate
        if duration < MIN_TURN_S:
            await self.error("audio_too_short", turn_id=turn_id)
            await self.set_state("idle")
            return
        if duration > MAX_TURN_S:
            await self.error("audio_too_long", turn_id=turn_id)
            await self.set_state("idle")
            return
        await self.set_state("transcribing", turn_id)
        audio16 = await asyncio.to_thread(resample, audio, rate, STT_RATE)
        stt_started = time.perf_counter()
        try:
            transcript = await self.core.gpu.run(self.core.stt.transcribe, audio16)
        except Exception:
            log.exception("speech recognition failed")
            await self.error("stt_failed", turn_id=turn_id, retryable=True)
            await self.set_state("idle")
            return
        stt_ms = (time.perf_counter() - stt_started) * 1000.0
        threshold = self.core.settings.get().low_conf_threshold
        words = [
            WordOut(w=w.w, start=round(w.start, 3), end=round(w.end, 3), p=round(w.p, 4))
            for w in transcript.words
        ]
        low_conf = [i for i, w in enumerate(transcript.words) if w.p < threshold]
        text = transcript.text.strip()
        await self.send(UserTranscript(turn_id=turn_id, text=text, words=words, low_conf=low_conf))
        if not text:
            await self.set_state("idle")
            return

        audio_rel = await asyncio.to_thread(self._store_audio, session.id, turn_id, audio16)
        self.core.sessions.add_turn(
            session_id=session.id,
            role="user",
            text=text,
            turn_id=turn_id,
            words=[w.model_dump() for w in words],
            audio_path=audio_rel,
        )
        unsure = [transcript.words[i].w for i in low_conf]
        session.history.append({"role": "user", "content": tutor_prompt.user_message(text, unsure)})
        session.last_user_turn_id = turn_id

        # The mode is read per turn: switching corrections off takes effect immediately.
        if self.core.settings.get().correction_mode == "off":
            await self.send(Corrections(turn_id=turn_id, status="disabled"))
            self.core.sessions.update_turn(turn_id, corrections_status="disabled")
        else:
            self._spawn(
                self.correct(session, turn_id, text, unsure, session.last_tutor_text),
                self.corrector_tasks,
            )
        await self.set_state("thinking", turn_id)
        self.reply_task = asyncio.create_task(
            self.generate_reply(session, turn_id, received, stt_ms)
        )

    def _store_audio(self, session_id: str, turn_id: str, audio16: np.ndarray) -> str | None:
        if self.core.settings.get().audio_retention_days <= 0:
            return None
        rel = f"audio/{session_id}/{turn_id}.wav"
        write_wav(self.core.data_dir / rel, audio16, STT_RATE)
        return rel

    async def retry(self, user_turn_id: str) -> None:
        session = self.session
        if (
            session is None
            or not session.retry_pending
            or session.last_user_turn_id != user_turn_id
        ):
            await self.error("bad_request", turn_id=user_turn_id)
            return
        session.retry_pending = False
        await self.set_state("thinking", user_turn_id)
        self.reply_task = asyncio.create_task(
            self.generate_reply(session, user_turn_id, time.perf_counter(), None)
        )

    # --- reply -----------------------------------------------------------------------------

    def build_messages(self, session: SessionCtx) -> list[dict[str, str]]:
        messages = [{"role": "system", "content": session.system_prompt}]
        if session.history_summary:
            messages.append(
                {
                    "role": "system",
                    "content": tutor_prompt.SUMMARY_SYSTEM.format(summary=session.history_summary),
                }
            )
        messages.extend(session.history)
        return messages

    async def generate_reply(
        self, session: SessionCtx, user_turn_id: str, received: float, stt_ms: float | None
    ) -> None:
        reply = ReplyCtx(turn_id=new_id(), user_turn_id=user_turn_id, started=received)
        reply.timing = {"stt_ms": stt_ms}
        self.reply = reply
        try:
            _row, endpoint = self.core.active_endpoint()
        except NoActiveConnection:
            session.retry_pending = True
            await self.error("no_active_connection", turn_id=user_turn_id)
            await self.set_state("idle")
            return
        except LLMError as e:
            session.retry_pending = True
            await self.error(e.code, turn_id=user_turn_id, retryable=e.retryable)
            await self.set_state("idle")
            return

        messages = self.build_messages(session)
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        llm_started = time.perf_counter()
        failure: LLMError | None = None

        async def produce() -> None:
            nonlocal failure
            splitter = SentenceSplitter()
            first = True
            try:
                async for fragment in self.core.llm.stream_chat(endpoint, messages, max_tokens=400):
                    reply.text_parts.append(fragment)
                    await self.send(TutorDelta(turn_id=reply.turn_id, text=fragment))
                    for sentence in splitter.feed(fragment):
                        if first:
                            reply.timing["llm_first_sentence_ms"] = (
                                time.perf_counter() - llm_started
                            ) * 1000.0
                            first = False
                        await queue.put(sentence)
                for sentence in splitter.flush():
                    if first:
                        reply.timing["llm_first_sentence_ms"] = (
                            time.perf_counter() - llm_started
                        ) * 1000.0
                        first = False
                    await queue.put(sentence)
            except LLMError as e:
                failure = e
            finally:
                await queue.put(None)

        producer = asyncio.create_task(produce())
        try:
            await self._speak_from_queue(reply, queue)
        finally:
            if not producer.done():
                producer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await producer

        if failure is not None and not reply.sentences_sent:
            session.retry_pending = True
            log.warning("tutor reply failed: %s %s", failure.code, failure.detail)
            await self.error(
                failure.code,
                turn_id=user_turn_id,
                retryable=failure.retryable,
                detail=failure.detail,
            )
            self.reply = None
            await self.set_state("idle")
            return
        await self._finish_reply(session, reply, interrupted=failure is not None)

    async def speak_fixed(self, text: str) -> None:
        """Speak a known text without the LLM (session opener)."""
        session = self.session
        if session is None:
            return
        reply = ReplyCtx(turn_id=new_id(), user_turn_id=None, started=time.perf_counter())
        self.reply = reply
        reply.text_parts.append(text)
        await self.set_state("thinking", reply.turn_id)
        await self.send(TutorDelta(turn_id=reply.turn_id, text=text))
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        splitter = SentenceSplitter()
        for sentence in [*splitter.feed(text), *splitter.flush()]:
            queue.put_nowait(sentence)
        queue.put_nowait(None)
        await self._speak_from_queue(reply, queue, send_timing=False)
        await self._finish_reply(session, reply, interrupted=False)

    async def _speak_from_queue(
        self, reply: ReplyCtx, queue: asyncio.Queue[str | None], send_timing: bool = True
    ) -> None:
        settings = self.core.settings.get()
        tts_failed_reported = False
        while True:
            sentence = await queue.get()
            if sentence is None:
                return
            spoken = clean_for_speech(sentence)
            if not spoken:
                continue
            if has_cyrillic(spoken):
                log.warning("tutor sentence contains Cyrillic; not synthesized")
                continue
            tts_started = time.perf_counter()
            try:
                result = await self.core.gpu.run(
                    self.core.tts.synthesize, spoken, voice=settings.voice, speed=settings.speed
                )
            except CyrillicTextError:
                continue
            except Exception:
                log.exception("speech synthesis failed")
                if not tts_failed_reported:
                    await self.error("tts_failed", turn_id=reply.turn_id)
                    tts_failed_reported = True
                continue
            pcm = float_to_pcm16(result.audio)
            index = len(reply.sentences_sent)
            reply.sentences_sent.append(clean_for_display(sentence))
            first = index == 0
            if first:
                reply.timing["tts_first_ms"] = (time.perf_counter() - tts_started) * 1000.0
            await self.send(
                TutorSentence(
                    turn_id=reply.turn_id,
                    index=index,
                    text=clean_for_display(sentence),
                    audio=AudioOut(sample_rate=TTS_RATE, bytes=len(pcm)),
                ),
                pcm,
            )
            if first:
                reply.timing["first_audio_ms"] = (time.perf_counter() - reply.started) * 1000.0
                await self.set_state("speaking", reply.turn_id)
                if send_timing and reply.user_turn_id is not None:
                    await self.send(
                        Timing(
                            turn_id=reply.user_turn_id,
                            stt_ms=_r(reply.timing.get("stt_ms")),
                            llm_first_sentence_ms=_r(reply.timing.get("llm_first_sentence_ms")),
                            tts_first_ms=_r(reply.timing.get("tts_first_ms")),
                            first_audio_ms=_r(reply.timing.get("first_audio_ms")),
                        )
                    )

    async def _finish_reply(self, session: SessionCtx, reply: ReplyCtx, interrupted: bool) -> None:
        full = clean_for_display("".join(reply.text_parts))
        text = " ".join(reply.sentences_sent) if interrupted else full
        reply.finished = True
        await self.send(TutorDone(turn_id=reply.turn_id, text=text, interrupted=interrupted))
        self._record_tutor_turn(session, reply, text, interrupted)
        if not reply.sentences_sent:
            await self.set_state("idle")
        self._spawn(self.fold_history(session))

    def _record_tutor_turn(
        self, session: SessionCtx, reply: ReplyCtx, text: str, interrupted: bool
    ) -> None:
        if text:
            session.history.append({"role": "assistant", "content": text})
            session.last_tutor_text = text
        session.retry_pending = False
        tid = self.core.sessions.add_turn(
            session_id=session.id, role="tutor", text=text, turn_id=reply.turn_id
        )
        timing = {k: _r(v) for k, v in reply.timing.items()}
        self.core.sessions.update_turn(tid, timing=timing, interrupted=interrupted)

    def _record_playback(self, msg: PlaybackStarted) -> None:
        if msg.client_first_audio_ms is None:
            return
        row = self.core.sessions.get_turn(msg.turn_id)
        if row is None:
            return
        timing = json.loads(row["timing"]) if row["timing"] else {}
        timing["client_first_audio_ms"] = round(msg.client_first_audio_ms, 1)
        self.core.sessions.update_turn(msg.turn_id, timing=timing)

    async def interrupt(self, record: bool = True) -> None:
        task = self.reply_task
        reply = self.reply
        if task is None or task.done():
            if self.state in ("thinking", "speaking"):
                await self.set_state("idle")
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        self.reply_task = None
        session = self.session
        if reply is not None and not reply.finished:
            text = " ".join(reply.sentences_sent)
            reply.finished = True
            await self.send(TutorDone(turn_id=reply.turn_id, text=text, interrupted=True))
            if record and session is not None and (text or reply.user_turn_id is None):
                self._record_tutor_turn(session, reply, text, True)
        await self.set_state("idle")

    # --- corrector -------------------------------------------------------------------------

    async def correct(
        self, session: SessionCtx, turn_id: str, text: str, unsure: list[str], tutor_said: str
    ) -> None:
        try:
            _row, endpoint = self.core.active_endpoint()
        except (NoActiveConnection, LLMError):
            await self.send(Corrections(turn_id=turn_id, status="failed"))
            self.core.sessions.update_turn(turn_id, corrections_status="failed")
            return
        result = await run_corrector(
            self.core.llm,
            endpoint,
            level=session.level,
            tutor_said=tutor_said,
            learner_said=text,
            unsure_words=unsure,
        )
        if result.items:
            self.core.sessions.add_corrections(session.id, turn_id, result.items)
        self.core.sessions.update_turn(
            turn_id, corrections_status=result.status, better=result.better
        )
        await self.send(
            Corrections(
                turn_id=turn_id,
                status=result.status,
                items=[CorrectionItem(**i) for i in result.items],
                better=result.better,
            )
        )

    # --- history folding -------------------------------------------------------------------

    async def fold_history(self, session: SessionCtx) -> None:
        if len(session.history) <= tutor_prompt.HISTORY_LIMIT:
            return
        async with self._fold_lock:
            if len(session.history) <= tutor_prompt.HISTORY_LIMIT:
                return
            old = session.history[: tutor_prompt.FOLD_BATCH]
            transcript = "\n".join(
                f"{'Tutor' if m['role'] == 'assistant' else 'Learner'}: {m['content']}" for m in old
            )
            prompt = tutor_prompt.SUMMARY_REQUEST.format(
                previous_summary=session.history_summary, old_turns=transcript
            )
            try:
                _row, endpoint = self.core.active_endpoint()
                summary = await self.core.llm.complete(
                    endpoint, [{"role": "user", "content": prompt}], max_tokens=300
                )
            except (NoActiveConnection, LLMError) as e:
                log.warning("history folding skipped: %s", e)
                return
            summary = clean_for_display(summary)
            if not summary:
                return
            session.history_summary = summary
            del session.history[: tutor_prompt.FOLD_BATCH]
            self.core.sessions.set_history_summary(session.id, summary)


def _r(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None


def new_request_id() -> str:
    return str(uuid.uuid4())
