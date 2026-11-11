"""Pronunciation service of the core: model lifecycle, assessment, storage, statistics."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from trainer.data_files import reading_phrases
from trainer.pron import catalog
from trainer.pron.assess import DEFAULT_THRESHOLDS, NoSpeechError, build_result
from trainer.pron.engine import AlignmentError, PronEngine
from trainer.pron.phoneme_model import PhonemeModel
from trainer.pron.reference import build_reference
from trainer.speech.audio import STT_RATE, FloatAudio, write_wav
from trainer.storage.repo import new_id, now_iso

log = logging.getLogger(__name__)

OFFSET_STEP = 0.75  # one «Это было верно» raises the margin for that sound by this much
OFFSET_MAX = 6.0
MIN_FREE_VRAM_MB = 400
CONFIDENT_WORD_P = 0.9  # conversation: only words recognized at least this confidently
MAX_TEXT_CHARS = 600


class PronError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PronService:
    def __init__(self, core: Any) -> None:
        self.core = core
        self.model = PhonemeModel()
        self.engine = PronEngine(self.model)
        self._load_lock = asyncio.Lock()

    # --- model lifecycle -------------------------------------------------------------------

    async def ensure_loaded(self) -> None:
        if self.model.loaded:
            return
        async with self._load_lock:
            if self.model.loaded:
                return
            self.core.models.phoneme = "loading"
            started = time.perf_counter()
            try:
                await self.core.gpu.run(self.model.load)
            except Exception:
                self.core.models.phoneme = "error"
                log.exception("failed to load the phoneme model")
                raise
            self.core.models.phoneme = "ready"
            log.info("phoneme model ready in %.0f ms", (time.perf_counter() - started) * 1000)

    def _unload_if_low_memory(self) -> None:
        """Runs on the GPU thread: free the model when video memory is nearly exhausted."""
        try:
            import torch

            if not torch.cuda.is_available():
                return
            free, _total = torch.cuda.mem_get_info()
            if free < MIN_FREE_VRAM_MB * 2**20:
                self.model.unload()
                self.core.models.phoneme = "not_loaded"
                log.warning("phoneme model unloaded: video memory is low")
        except Exception:
            log.exception("video memory check failed")

    # --- assessment ------------------------------------------------------------------------

    def offsets(self) -> dict[str, float]:
        rows = self.core.db.all("SELECT phone, sensitivity_offset FROM pron_sound")
        return {r["phone"]: float(r["sensitivity_offset"]) for r in rows if r["sensitivity_offset"]}

    def _analyze(
        self,
        source_words: list[str],
        audio: FloatAudio,
        offsets: dict[str, float],
        confident: list[bool] | None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        ref = build_reference(source_words)
        if not ref.phones:
            raise PronError("pron_no_text")
        if confident is not None:
            for p in ref.phones:
                if not confident[ref.words[p.word].source_index]:
                    p.scored = False
                    p.competitors = []
        try:
            evidence = self.engine.analyze(audio, ref)
            result = build_result(ref, evidence, source_words, offsets=offsets)
            strict = (
                build_result(ref, evidence, source_words, offsets=offsets, strict=True)
                if confident is not None
                else None
            )
        except AlignmentError as e:
            raise PronError("pron_no_speech") from e
        except NoSpeechError as e:
            raise PronError("pron_no_speech") from e
        finally:
            self._unload_if_low_memory()
        return result, strict

    async def assess(
        self,
        text: str,
        audio_16k: FloatAudio,
        *,
        source: str = "reading",
        phrase_id: str | None = None,
    ) -> dict[str, Any]:
        text = text.strip()
        if not text or len(text) > MAX_TEXT_CHARS:
            raise PronError("pron_no_text")
        await self.ensure_loaded()
        started = time.perf_counter()
        result, _ = await self.core.gpu.run(
            self._analyze, text.split(), audio_16k, self.offsets(), None
        )
        result["analysis_ms"] = round((time.perf_counter() - started) * 1000)
        result["text"] = text
        attempt_id = await asyncio.to_thread(
            self._store, result, audio_16k, source=source, phrase_id=phrase_id, text=text
        )
        result["attempt_id"] = attempt_id
        return result

    async def assess_turn(
        self,
        words: list[dict[str, Any]],
        audio_16k: FloatAudio,
        *,
        session_id: str,
        turn_id: str,
    ) -> list[dict[str, Any]]:
        """Free conversation: cautious marks. Returns pron.flags items (strict errors only)."""
        source_words = [w["w"] for w in words]
        confident = [float(w["p"]) >= CONFIDENT_WORD_P for w in words]
        if not any(confident):
            return []
        await self.ensure_loaded()
        try:
            result, strict = await self.core.gpu.run(
                self._analyze, source_words, audio_16k, self.offsets(), confident
            )
        except PronError:
            return []
        assert strict is not None
        flags: list[dict[str, Any]] = []
        for word in strict["words"]:
            tip = word["tip"]
            if word["level"] != "error" or tip is None:
                continue
            flags.append(
                {
                    "word_index": word["source_index"],
                    "word": word["w"],
                    "expected": tip["expected"],
                    "heard": tip["heard"],
                    "tip_ru": " ".join(x for x in (tip["title_ru"], tip["tip_ru"]) if x),
                    "catalog_id": tip["catalog_id"],
                }
            )
        result["flags"] = flags
        result["text"] = " ".join(source_words)
        await asyncio.to_thread(
            self._store,
            result,
            None,
            source="conversation",
            phrase_id=None,
            text=result["text"],
            session_id=session_id,
            turn_id=turn_id,
        )
        return flags

    # --- storage and statistics --------------------------------------------------------------

    def _store(
        self,
        result: dict[str, Any],
        audio_16k: FloatAudio | None,
        *,
        source: str,
        phrase_id: str | None,
        text: str,
        session_id: str | None = None,
        turn_id: str | None = None,
    ) -> str:
        core = self.core
        attempt_id = new_id()
        audio_rel: str | None = None
        if audio_16k is not None and core.settings.get().audio_retention_days > 0:
            audio_rel = f"audio/pron/{attempt_id}.wav"
            write_wav(core.data_dir / audio_rel, audio_16k, STT_RATE)
        core.db.execute(
            "INSERT INTO pron_attempt(id, source, phrase_id, text, audio_path, score, details,"
            " session_id, turn_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                attempt_id,
                source,
                phrase_id,
                text,
                audio_rel,
                result["score"],
                json.dumps(result, ensure_ascii=False),
                session_id,
                turn_id,
                now_iso(),
            ),
        )
        counts: dict[str, list[int]] = {}
        for word in result["words"]:
            for ph in word["phones"]:
                if not ph["scored"]:
                    continue
                c = counts.setdefault(ph["cls"], [0, 0, 0])
                c[0] += 1
                c[1] += ph["level"] == "error"
                c[2] += ph["level"] == "approx"
        with core.db.transaction() as conn:
            for cls, (n, err, apx) in counts.items():
                conn.execute(
                    "INSERT INTO pron_sound(phone, attempts, errors, approx, updated_at)"
                    " VALUES (?, ?, ?, ?, ?) ON CONFLICT(phone) DO UPDATE SET"
                    " attempts = attempts + excluded.attempts, errors = errors + excluded.errors,"
                    " approx = approx + excluded.approx, updated_at = excluded.updated_at",
                    (cls, n, err, apx, now_iso()),
                )
        return attempt_id

    def feedback(self, attempt_id: str, word_index: int, phone_index: int) -> dict[str, Any]:
        """«Это было верно»: clear the mark and make this sound less sensitive from now on."""
        db = self.core.db
        row = db.one("SELECT * FROM pron_attempt WHERE id = ?", (attempt_id,))
        if row is None:
            raise PronError("not_found")
        details = json.loads(row["details"])
        try:
            word = details["words"][word_index]
            phone = word["phones"][phone_index]
        except (IndexError, KeyError, TypeError) as e:
            raise PronError("not_found") from e
        was = phone["level"]
        cls = phone["cls"]
        if was != "ok":
            phone["level"] = "ok"
            phone["heard"] = phone["expected"]
            phone["overridden"] = True
            levels = [p["level"] for p in word["phones"]]
            word["level"] = (
                "error" if "error" in levels else "approx" if "approx" in levels else "ok"
            )
            if word["level"] == "ok":
                word["tip"] = None
            db.execute(
                "UPDATE pron_attempt SET details = ? WHERE id = ?",
                (json.dumps(details, ensure_ascii=False), attempt_id),
            )
            column = "errors" if was == "error" else "approx"
            db.execute(
                f"UPDATE pron_sound SET {column} = MAX({column} - 1, 0) WHERE phone = ?", (cls,)
            )
        db.execute(
            "INSERT INTO pron_sound(phone, sensitivity_offset, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(phone) DO UPDATE SET sensitivity_offset ="
            " MIN(sensitivity_offset + excluded.sensitivity_offset, ?),"
            " updated_at = excluded.updated_at",
            (cls, OFFSET_STEP, now_iso(), OFFSET_MAX),
        )
        offset = db.one("SELECT sensitivity_offset FROM pron_sound WHERE phone = ?", (cls,))
        details["attempt_id"] = attempt_id
        return {
            "phone": cls,
            "sensitivity_offset": float(offset["sensitivity_offset"]),
            "attempt": details,
        }

    def sounds(self, days: int = 7) -> list[dict[str, Any]]:
        cutoff = (datetime.now(UTC) - timedelta(days=max(1, days))).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        rows = self.core.db.all(
            "SELECT details FROM pron_attempt WHERE created_at >= ? ORDER BY created_at", (cutoff,)
        )
        stats: dict[str, dict[str, Any]] = {}
        for row in rows:
            for word in json.loads(row["details"]).get("words", []):
                for ph in word["phones"]:
                    if not ph.get("scored"):
                        continue
                    s = stats.setdefault(
                        ph["cls"],
                        {
                            "phone": ph["cls"],
                            "attempts": 0,
                            "errors": 0,
                            "approx": 0,
                            "example": "",
                        },
                    )
                    s["attempts"] += 1
                    if ph["level"] == "error":
                        s["errors"] += 1
                        s["example"] = word["w"]
                    elif ph["level"] == "approx":
                        s["approx"] += 1
                        s["example"] = s["example"] or word["w"]
        offsets = self.offsets()
        out = [s for s in stats.values() if s["errors"] or s["approx"]]
        for s in out:
            s["sensitivity_offset"] = offsets.get(s["phone"], 0.0)
        out.sort(key=lambda s: (-s["errors"], -s["approx"], s["phone"]))
        return out

    def session_sounds(self, session_id: str) -> list[dict[str, Any]]:
        """Pronunciation marks shown during a session, grouped by sound (for the summary)."""
        rows = self.core.db.all(
            "SELECT details FROM pron_attempt WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        )
        by_phone: dict[str, dict[str, Any]] = {}
        for row in rows:
            for flag in json.loads(row["details"]).get("flags", []):
                s = by_phone.setdefault(
                    flag["expected"], {"phone": flag["expected"], "count": 0, "words": []}
                )
                s["count"] += 1
                if flag["word"] not in s["words"]:
                    s["words"].append(flag["word"])
        return sorted(by_phone.values(), key=lambda s: -s["count"])

    # --- reading phrases ---------------------------------------------------------------------

    def phrases(self) -> list[dict[str, Any]]:
        out = []
        for p in reading_phrases()["phrases"]:
            targets = []
            for tid in p.get("targets", []):
                entry = catalog.entry_by_id(tid)
                if entry is not None:
                    targets.append({"id": tid, "title_ru": entry.title_ru})
            out.append({"id": p["id"], "text": p["text"], "targets": targets})
        return out

    def phrase_text(self, phrase_id: str) -> str | None:
        for p in reading_phrases()["phrases"]:
            if p["id"] == phrase_id:
                return str(p["text"])
        return None


THRESHOLDS = DEFAULT_THRESHOLDS
