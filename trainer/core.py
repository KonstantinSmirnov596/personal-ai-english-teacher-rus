"""Core services shared by HTTP routes and the WebSocket conversation."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

from trainer import __version__, keystore
from trainer.data_files import scenarios as builtin_scenarios
from trainer.llm.client import Endpoint, LLMClient, LLMError, Timeouts
from trainer.server.guard import AccessKeys
from trainer.speech.stt import SpeechRecognizer
from trainer.speech.tts import SpeechSynthesizer
from trainer.storage.db import Database
from trainer.storage.repo import ConnectionRepo, ScenarioRepo, SessionRepo, SettingsRepo

log = logging.getLogger(__name__)
T = TypeVar("T")


class GpuQueue:
    """All GPU model calls run one at a time on a single worker thread."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpu")

    async def run(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, partial(fn, *args, **kwargs))

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


@dataclass
class ModelStatus:
    stt: str = "not_loaded"  # not_loaded | loading | ready | error
    tts: str = "not_loaded"
    phoneme: str = "not_loaded"
    error: str = ""
    load_ms: dict[str, float] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.stt == "ready" and self.tts == "ready"


class NoActiveConnection(Exception):
    pass


class Core:
    def __init__(
        self,
        *,
        data_dir: Path,
        keys: AccessKeys,
        load_models: bool = True,
        llm_timeouts: Timeouts | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.keys = keys
        self.app_version = __version__
        data_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir = data_dir / "audio"
        self.db = Database(data_dir / "trainer.db")
        self.settings = SettingsRepo(self.db)
        self.connections = ConnectionRepo(self.db)
        self.scenarios = ScenarioRepo(self.db)
        self.sessions = SessionRepo(self.db)
        self.llm = LLMClient(llm_timeouts)
        self.gpu = GpuQueue()
        self.stt = SpeechRecognizer()
        self.tts = SpeechSynthesizer()
        self.models = ModelStatus()
        self.load_models = load_models
        self.models_ready = asyncio.Event()
        self.started_at = time.monotonic()
        # WebSocket presence, used by the launcher to exit after the window is closed.
        self.ws_count = 0
        self.ws_ever_connected = False
        self.ws_last_change = time.monotonic()
        self.current_ws: Any = None
        self._load_task: asyncio.Task[None] | None = None

    async def startup(self) -> None:
        self.scenarios.seed_builtin(builtin_scenarios())
        if self.load_models:
            self._load_task = asyncio.create_task(self._load_models())

    async def shutdown(self) -> None:
        await self.llm.aclose()
        self.gpu.shutdown()
        self.db.close()

    async def _load_models(self) -> None:
        for name, model in (("stt", self.stt), ("tts", self.tts)):
            setattr(self.models, name, "loading")
            started = time.perf_counter()
            try:
                await self.gpu.run(model.load)
            except Exception as e:  # missing GPU, missing models, broken install
                log.exception("failed to load %s model", name)
                setattr(self.models, name, "error")
                self.models.error = f"{name}: {type(e).__name__}"
                continue
            self.models.load_ms[name] = round((time.perf_counter() - started) * 1000)
            setattr(self.models, name, "ready")
            log.info("%s model ready in %.0f ms", name, self.models.load_ms[name])
        if self.models.ready:
            # Warm-up: the first call after loading is slower (CUDA kernels, caches).
            try:
                await self.gpu.run(self.tts.synthesize, "Hello there.")
                import numpy as np

                await self.gpu.run(self.stt.transcribe, np.zeros(16000, dtype=np.float32))
            except Exception:
                log.exception("model warm-up failed")
            self.models_ready.set()

    # --- connections ---------------------------------------------------------------------------

    def active_endpoint(self) -> tuple[Any, Endpoint]:
        row = self.connections.active()
        if row is None:
            raise NoActiveConnection
        key = keystore.load_key(int(row["id"]))
        if not key:
            raise LLMError("llm_unauthorized", "no key saved for the active connection")
        return row, endpoint_from_row(row, key)

    def connection_ref(self, row: Any) -> dict[str, Any]:
        return {"id": int(row["id"]), "name": row["name"], "model": row["model"]}


def endpoint_from_row(row: Any, key: str) -> Endpoint:
    import json

    return Endpoint(
        base_url=row["base_url"],
        model=row["model"],
        api_key=key,
        extra_params=json.loads(row["extra_params"] or "{}"),
        json_mode=bool(row["json_mode"]),
    )
