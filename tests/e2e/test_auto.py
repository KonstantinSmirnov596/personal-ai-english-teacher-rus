"""Stage 2.1 end-to-end: auto turn mode with Silero VAD in the window.

The fake microphone loops turn_pause_1200.wav: phrase A, 1.2 s silence, phrase B, 5 s silence.
With a 1.5 s pause threshold A and B form one turn; with 1.0 s they are split into two turns
(headphones on, so the detector keeps listening while the tutor answers the first part).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest

from tests.e2e.conftest import E2E, open_env

pytestmark = pytest.mark.e2e

PHRASE_A = "would like to talk about my new job"
PHRASE_B = "more interesting than the old one"


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[E2E]:
    yield from open_env(tmp_path_factory, "turn_pause_1200")


def user_texts(page: Any) -> list[str]:
    return [
        t.lower()
        for t in page.eval_on_selector_all(
            "[data-testid=turn-user] .turn-text", "els => els.map(e => e.textContent)"
        )
    ]


def wait_turn(page: Any, pred: Any, timeout: float = 90) -> list[str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        texts = user_texts(page)
        if pred(texts):
            return texts
        time.sleep(0.25)
    raise TimeoutError(f"no matching user turns: {user_texts(page)}")


def start_auto_session(env: E2E, pause_ms: int, headphones: bool) -> Any:
    with env.http() as c:
        if not c.get("/api/connections").json():
            c.post(
                "/api/connections",
                json={
                    "name": "Mock",
                    "base_url": env.mock.base_url,
                    "model": "m",
                    "api_key": env.key,
                },
            )
        c.patch(
            "/api/settings",
            json={
                "turn_mode": "auto",
                "pause_ms": pause_ms,
                "headphones": headphones,
                "correction_mode": "off",
            },
        )
    env.mock.script(text="Okay, tell me more.", chunk_delay_ms=5)
    env.wait_models()
    page = env.new_page()
    page.goto(env.lp.base + "/#/talk")
    page.click("[data-scenario=free_talk]")
    page.click("[data-testid=start-session]")
    page.wait_for_selector("[data-testid=panel][data-turn-mode=auto]", timeout=10_000)
    page.locator("[data-testid=turn-tutor] .turn-text:not(:empty)").first.wait_for(timeout=60_000)
    return page


def test_pause_below_threshold_keeps_one_turn(env: E2E) -> None:
    page = start_auto_session(env, pause_ms=1500, headphones=False)
    texts = wait_turn(page, lambda ts: any(PHRASE_A in t for t in ts))
    whole = next(t for t in texts if PHRASE_A in t)
    assert PHRASE_B in whole, texts
    env.screenshot("07-auto-one-turn")
    errors = [line for line in env.console if line.startswith("error")]
    assert not errors, errors


def test_pause_above_threshold_splits_turn(env: E2E) -> None:
    page = start_auto_session(env, pause_ms=1000, headphones=True)

    def split(ts: list[str]) -> bool:
        for i, t in enumerate(ts):
            if PHRASE_A in t and PHRASE_B not in t:
                return any(PHRASE_B in later for later in ts[i + 1 :])
        return False

    texts = wait_turn(page, split)
    first = next(i for i, t in enumerate(texts) if PHRASE_A in t and PHRASE_B not in t)
    assert any(PHRASE_B in t and PHRASE_A not in t for t in texts[first + 1 :]), texts
    env.screenshot("08-auto-split")
