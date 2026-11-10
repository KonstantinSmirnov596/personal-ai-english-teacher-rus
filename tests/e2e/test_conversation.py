"""Stage 1 end-to-end: connections screen, manual turn with the fake microphone, corrections,
connection failure with a manual switch (docs/PLAN.md, stage 1 acceptance)."""

from __future__ import annotations

import re
import time
from typing import Any

import pytest

from tests.e2e.conftest import E2E

pytestmark = pytest.mark.e2e

SILENCE = 10  # data-level below this is silence
CYRILLIC = re.compile(r"[Ѐ-ӿ]")

STATE_RECORDER = """
() => {
  const panel = document.querySelector('[data-testid=panel]');
  window.__seenStates = [panel.dataset.state];
  new MutationObserver(() => window.__seenStates.push(panel.dataset.state))
    .observe(panel, {attributes: true, attributeFilter: ['data-state']});
}
"""


def level(page: Any) -> int:
    return int(page.get_attribute("[data-testid=wave]", "data-level") or 0)


def wait_level(page: Any, *, above: bool, hold_s: float, timeout: float = 30) -> None:
    """Wait until the level stays above/below the silence threshold for hold_s seconds."""
    deadline = time.monotonic() + timeout
    since: float | None = None
    while time.monotonic() < deadline:
        ok = level(page) >= SILENCE if above else level(page) < SILENCE
        now = time.monotonic()
        if ok:
            since = since or now
            if now - since >= hold_s:
                return
        else:
            since = None
        time.sleep(0.05)
    raise TimeoutError(f"level did not stay {'above' if above else 'below'} threshold")


def add_connection_via_ui(page: Any, e2e: E2E, name: str, key: str) -> None:
    page.goto(e2e.lp.base + "/#/settings/connections")
    if not page.is_visible("[data-testid=conn-form]"):
        page.click("[data-testid=conn-add]")
    page.select_option("#conn-template", "openai_compatible")
    page.fill("#conn-name", name)
    page.fill("#conn-url", e2e.mock.base_url)
    page.fill("#conn-model", "mock-model")
    page.fill("[data-testid=conn-key-input]", key)
    page.click("[data-testid=conn-save]")
    page.wait_for_selector(f"[data-testid=conn-row]:has-text('{name}')")


def start_session(page: Any, e2e: E2E) -> None:
    page.goto(e2e.lp.base + "/#/talk")
    page.click("[data-scenario=interview_backend]")
    page.click("[data-testid=start-session]")
    page.wait_for_selector("[data-testid=turn-tutor] .turn-text:not(:empty)", timeout=60_000)
    page.wait_for_selector("[data-testid=panel][data-state=idle]", timeout=60_000)


def speak_one_turn(page: Any) -> None:
    """Space at silence, wait for one phrase of the looping file, Space again."""
    wait_level(page, above=False, hold_s=1.0)
    page.keyboard.press("Space")
    page.wait_for_selector("[data-testid=panel][data-state=listening]", timeout=5000)
    wait_level(page, above=True, hold_s=0.3)
    wait_level(page, above=False, hold_s=1.0)
    page.keyboard.press("Space")


def test_conversation_turn(e2e: E2E) -> None:
    page = e2e.new_page()
    page.goto(e2e.lp.base + "/#/talk")
    page.wait_for_selector("[data-testid=add-connection]")
    e2e.screenshot("01-no-connection")

    add_connection_via_ui(page, e2e, "Mock main", e2e.key)
    row = page.locator("[data-testid=conn-row]").first
    assert row.get_attribute("data-active") == "true"
    assert row.locator("[data-testid=conn-key-last4]").inner_text() == "••••" + e2e.key[-4:]
    assert e2e.key not in page.content()
    page.click("[data-testid=conn-test]")
    page.wait_for_selector("[data-testid=conn-test-result].test-ok", timeout=20_000)
    e2e.screenshot("02-connections")

    e2e.wait_models()
    start_session(page, e2e)
    page.evaluate(STATE_RECORDER)
    e2e.screenshot("03-session-started")

    speak_one_turn(page)
    page.wait_for_selector(
        "[data-testid=turn-user] .turn-text:has-text('worked on this project')", timeout=60_000
    )
    page.locator("[data-testid=turn-tutor] .turn-text:not(:empty)").nth(1).wait_for(timeout=60_000)
    page.wait_for_selector("[data-testid=panel][data-state=idle]", timeout=60_000)
    seen = page.evaluate("window.__seenStates")
    assert "listening" in seen and "speaking" in seen, seen
    tutor = page.locator("[data-testid=turn-tutor]").nth(1)
    assert "hardest part" in tutor.inner_text()
    first_audio = tutor.get_attribute("data-first-audio-ms")
    assert first_audio is not None and int(first_audio) > 0

    # Corrections: underline in the turn, card opens on click (default mode «по нажатию»).
    mark = page.locator("[data-testid=correction-mark]").first
    mark.wait_for(timeout=30_000)
    card = page.locator("[data-testid=correction-card]").first
    assert not card.is_visible()
    mark.click()
    assert card.is_visible()
    assert CYRILLIC.search(card.inner_text())
    e2e.screenshot("04-correction-card")

    # Russian only inside cards: no Cyrillic in any element marked lang="en".
    en_texts = page.eval_on_selector_all("[lang=en]", "els => els.map(e => e.textContent)")
    assert en_texts and not [t for t in en_texts if CYRILLIC.search(t)]
    ru_outside = page.evaluate(
        """() => [...document.querySelectorAll('[data-testid=turn-user], [data-testid=turn-tutor]')]
            .map(t => t.cloneNode(true))
            .map(c => { c.querySelectorAll('[lang=ru], .turn-label, .turn-actions, .turn-note')
                         .forEach(n => n.remove()); return c.textContent; })"""
    )
    assert not [t for t in ru_outside if CYRILLIC.search(t)]
    assert not [line for line in e2e.console if line.startswith("error")], e2e.console


def test_connection_failure_offers_manual_switch(e2e: E2E) -> None:
    page = e2e.new_page()
    add_connection_via_ui(page, e2e, "Broken key", e2e.key + "-wrong")
    with e2e.http() as c:
        rows = c.get("/api/connections").json()
        broken = next(r for r in rows if r["name"] == "Broken key")
        good = next(r for r in rows if r["name"] == "Mock main")
        c.post(f"/api/connections/{broken['id']}/activate")
    e2e.wait_models()
    start_session(page, e2e)
    speak_one_turn(page)
    banner = page.locator("[data-testid=error-banner][data-code=llm_unauthorized]")
    banner.wait_for(timeout=60_000)
    switch = banner.locator(f"[data-testid=switch-connection][data-connection-id='{good['id']}']")
    switch.wait_for(timeout=10_000)
    e2e.screenshot("05-error-banner")
    time.sleep(1.0)
    with e2e.http() as c:  # no automatic switching
        active = next(r for r in c.get("/api/connections").json() if r["is_active"])
        assert active["id"] == broken["id"]
    switch.click()
    page.locator("[data-testid=turn-tutor] .turn-text:not(:empty)").nth(1).wait_for(timeout=60_000)
    with e2e.http() as c:
        active = next(r for r in c.get("/api/connections").json() if r["is_active"])
        assert active["id"] == good["id"]


def test_history_lists_sessions(e2e: E2E) -> None:
    page = e2e.new_page()
    page.goto(e2e.lp.base + "/#/history")
    rows = page.locator("[data-testid=session-row]")
    rows.first.wait_for(timeout=10_000)
    assert rows.count() >= 2
    rows.first.locator("a").first.click()
    page.locator("article.turn").first.wait_for(timeout=10_000)
    assert page.locator("article.turn.tutor").count() >= 1
    e2e.screenshot("06-history")
