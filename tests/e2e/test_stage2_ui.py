"""Stage 2 window features end to end: helpers, replay, summary with exercise, vocabulary,
settings sections."""

from __future__ import annotations

import re
import time
from typing import Any

import pytest

from tests.e2e.conftest import E2E
from tests.e2e.test_conversation import speak_one_turn, start_session

pytestmark = pytest.mark.e2e
CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def ensure_connection(e2e: E2E) -> None:
    with e2e.http() as c:
        if not c.get("/api/connections").json():
            c.post(
                "/api/connections",
                json={
                    "name": "Mock",
                    "base_url": e2e.mock.base_url,
                    "model": "m",
                    "api_key": e2e.key,
                },
            )
        c.patch("/api/settings", json={"turn_mode": "manual", "correction_mode": "on_click"})


def test_helpers_summary_and_vocab(e2e: E2E) -> None:
    ensure_connection(e2e)
    e2e.mock.script()
    page = e2e.new_page()
    e2e.wait_models()
    start_session(page, e2e)
    speak_one_turn(page)
    page.locator("[data-testid=turn-tutor] .turn-text:not(:empty)").nth(1).wait_for(timeout=60_000)
    page.wait_for_selector("[data-testid=panel][data-state=idle]", timeout=60_000)

    # Translation of a tutor turn: Russian text only, below the turn.
    page.locator("[data-testid=tutor-translate]").first.click()
    tr = page.locator("[data-testid=translation]").first
    page.wait_for_function  # noqa: B018 - documentation: CSP forbids string predicates
    tr.filter(has_text="Тестовый перевод").wait_for(timeout=20_000)
    assert tr.get_attribute("lang") == "ru"

    # Hint: English options.
    page.click("[data-testid=helper-hint]")
    hint = page.locator("[data-testid=hint-card] li")
    hint.first.wait_for(timeout=20_000)
    assert hint.count() == 2
    assert all(not CYRILLIC.search(t) for t in hint.all_inner_texts())

    # How to say: Russian in, English out.
    page.click("[data-testid=helper-how]")
    page.fill("[data-testid=how-input]", "Можно задать вопрос?")
    page.keyboard.press("Enter")
    page.locator("[data-testid=how-card] .strong").filter(has_text="ask a question").wait_for(
        timeout=20_000
    )
    e2e.screenshot("09-helpers")

    # Replay: the tutor's turn sounds again (waveform switches to the tutor source).
    page.locator("[data-testid=tutor-slower]").first.click()
    page.wait_for_selector("[data-testid=wave][data-source=tutor]", timeout=20_000)
    page.wait_for_selector("[data-testid=wave][data-source=mic]", timeout=30_000)

    # Summary with key takeaway and exercise.
    page.click("[data-testid=end-session]")
    page.wait_for_selector("[data-testid=summary]", timeout=60_000)
    page.wait_for_selector("[data-testid=key-takeaway]", timeout=10_000)
    assert "for / since" in page.inner_text("[data-testid=key-takeaway]")
    e2e.screenshot("10-summary")

    # Exercise answered by voice (the looping file says the turn_basic phrase).
    say = page.locator("[data-testid=exercise-say]")
    say.click()
    time.sleep(3.5)
    say.click()
    result = page.locator("[data-testid=exercise-result]")
    result.filter(has_text="Тестовая проверка").wait_for(timeout=30_000)

    # Save a tutor phrase into the vocabulary.
    add = page.locator("[data-testid=add-vocab]").first
    add.click()
    page.locator("[data-testid=add-vocab]:disabled").first.wait_for(timeout=10_000)
    page.goto(e2e.lp.base + "/#/vocab")
    page.locator("[data-testid=vocab-row]").first.wait_for(timeout=10_000)
    e2e.screenshot("11-vocab")


def test_settings_sections(e2e: E2E) -> None:
    ensure_connection(e2e)
    page = e2e.new_page()
    for section in ("sound", "conversation", "corrections", "data"):
        page.goto(e2e.lp.base + f"/#/settings/{section}")
        page.wait_for_selector(f"[data-section={section}][aria-current=page]")
    page.goto(e2e.lp.base + "/#/settings/conversation")
    slider = page.locator("[data-testid=pause-slider]")
    slider.wait_for()
    slider.fill("2200")
    slider.dispatch_event("change")
    page.wait_for_selector("[data-testid=settings-status]:has-text('Сохранено')")
    page.check("#set-headphones")
    time.sleep(0.5)
    with e2e.http() as c:
        s: dict[str, Any] = c.get("/api/settings").json()
    assert s["pause_ms"] == 2200 and s["headphones"] is True
    page.goto(e2e.lp.base + "/#/settings/sound")
    page.wait_for_selector("[data-testid=voice-select]")
    page.select_option("[data-testid=voice-select]", "am_michael")
    time.sleep(0.5)
    with e2e.http() as c:
        assert c.get("/api/settings").json()["voice"] == "am_michael"
        c.patch("/api/settings", json={"voice": "af_heart", "pause_ms": 1500, "headphones": False})
    e2e.screenshot("12-settings-sound")
