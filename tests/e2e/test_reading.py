"""Stage 3 end-to-end: the «Чтение» screen with a fake microphone.

The microphone loops phrase p01 read with two planted mistakes: "work" said with /v/ and
"third" said with /s/ (tests/fixtures/phrases.json, reading_p01_accent).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.e2e.conftest import E2E, open_env
from tests.e2e.test_conversation import wait_level

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[E2E]:
    yield from open_env(tmp_path_factory, "reading_p01_accent")


def test_reading_marks_sounds_and_accepts_feedback(env: E2E) -> None:
    env.wait_models()
    page = env.new_page()
    page.goto(env.lp.base + "/#/reading")
    page.wait_for_selector("[data-testid=reading-text][data-phrase-id=p01]")
    env.screenshot("13-reading-empty")

    # the first click opens the microphone; cancel and wait for the gap in the looping file
    page.click("[data-testid=reading-mic]")
    page.wait_for_selector("[data-testid=reading-panel][data-state=listening]", timeout=10_000)
    page.keyboard.press("Escape")
    page.wait_for_selector("[data-testid=reading-panel][data-state=idle]")
    wait_level(page, above=False, hold_s=1.0)
    page.keyboard.press("Space")
    page.wait_for_selector("[data-testid=reading-panel][data-state=listening]")
    wait_level(page, above=True, hold_s=0.3)
    wait_level(page, above=False, hold_s=1.0)
    page.keyboard.press("Space")

    page.wait_for_selector("[data-testid=read-word]", timeout=60_000)
    errors = page.locator("[data-testid=read-word][data-level=error]")
    assert sorted(t.strip(".").lower() for t in errors.all_inner_texts()) == ["third", "work"]
    assert page.locator("[data-testid=reading-score]").is_visible()

    # the first wrong word is opened: what was heard, the tip, the minimal pair
    detail = page.locator("[data-testid=word-detail]")
    assert detail.locator("h2").inner_text() == "work"
    title = page.inner_text("[data-testid=word-tip-title]")
    assert "/v/" in title and "/w/" in title
    assert detail.locator(".phone-chip[data-level=error]").count() == 1
    assert "west — vest" in detail.inner_text()
    env.screenshot("14-reading-result")

    # the sidebar counts marked sounds
    page.locator("[data-testid=sound-row][data-phone=w]").wait_for(timeout=10_000)

    # «Это было верно» clears the mark and makes the sound less sensitive
    page.click("[data-testid=was-correct]")
    page.locator("[data-testid=reading-message]").filter(has_text="мягче").wait_for(timeout=10_000)
    assert errors.count() == 1
    with env.http() as c:
        sounds = {s["phone"]: s for s in c.get("/api/pron/sounds?days=7").json()}
    assert "w" not in sounds and sounds["θ"]["errors"] == 1  # the cleared mark is not counted

    # other words open on click; a correct word says so
    page.locator("[data-testid=read-word]", has_text="third").click()
    title = page.inner_text("[data-testid=word-tip-title]")
    assert "/s/" in title and "/θ/" in title
    page.locator("[data-testid=read-word]", has_text="week").click()
    assert page.locator("[data-testid=word-tip-title]").count() == 0

    assert not page.locator("[data-testid=reading-mine]").is_disabled()
    page.click("[data-testid=reading-next]")
    page.wait_for_selector("[data-testid=reading-text][data-phrase-id=p02]")
    assert page.locator("[data-testid=read-word]").count() == 0
    problems = [line for line in env.console if line.startswith("error")]
    assert not problems, problems
