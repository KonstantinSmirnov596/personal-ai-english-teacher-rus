"""Stage 3.9 end-to-end: a clear pronunciation mistake is highlighted in the conversation.

The microphone loops "I think this is a very good idea" with "very" said as "wery".
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.e2e.conftest import E2E, open_env
from tests.e2e.test_conversation import speak_one_turn, start_session

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[E2E]:
    yield from open_env(tmp_path_factory, "turn_accent")


def test_pronunciation_mark_in_conversation(env: E2E) -> None:
    with env.http() as c:
        c.post(
            "/api/connections",
            json={"name": "Mock", "base_url": env.mock.base_url, "model": "m", "api_key": env.key},
        )
    env.wait_models()
    page = env.new_page()
    start_session(page, env)
    speak_one_turn(page)
    mark = page.locator("[data-testid=pron-mark]")
    mark.first.wait_for(timeout=90_000)
    assert mark.count() == 1
    assert mark.first.inner_text().lower() == "very"
    mark.first.click()
    card = page.locator("[data-testid=pron-card]")
    text = card.inner_text()
    assert "/w/" in text and "/v/" in text
    assert card.get_attribute("lang") == "ru"
    env.screenshot("15-pron-mark")
    # the grammar underline and the pronunciation fill coexist in one turn
    assert page.locator("[data-testid=correction-mark]").count() >= 1
