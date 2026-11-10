"""Corrector: schema, retry, quote check, filters (plan item 1.7)."""

from __future__ import annotations

import pytest

from tests.mock_llm import MockLLM
from trainer.corrector import locate, parse_response, postprocess, run_corrector
from trainer.data_files import pattern_keys
from trainer.llm.client import Endpoint, LLMClient
from trainer.prompts.corrector import build_system_prompt

TEXT = "I have worked on this project since two years and we was migrating the system."


def test_parse_valid_and_invalid() -> None:
    assert parse_response("") is None
    assert parse_response("   ") is None
    assert parse_response("{not json") is None
    assert parse_response("[1, 2]") is None
    raw = parse_response('{"items": [], "better": ""}')
    assert raw is not None and raw.items == []
    wrapped = parse_response('Sure! {"items": [], "better": ""} hope it helps')
    assert wrapped is not None


def test_locate_exact_and_relaxed() -> None:
    assert locate("since two years", TEXT) == (TEXT.index("since"), TEXT.index("since") + 15)
    start, end = locate("We  was", TEXT) or (None, None)
    assert TEXT[start:end] == "we was"
    assert locate("not there", TEXT) is None


def _items(*items: dict) -> object:
    return parse_response(__import__("json").dumps({"items": list(items), "better": "Better."}))


def test_postprocess_rules() -> None:
    raw = _items(
        {
            "quote": "since two years",
            "fix": "for two years",
            "type": "grammar",
            "note_ru": "For с длительностью.",
            "pattern": "for_since",
        },
        {
            "quote": "we was migrating",
            "fix": "we were migrating",
            "type": "grammar",
            "note_ru": "We — were.",
            "pattern": "unknown_key",
        },
        {
            "quote": "the system",
            "fix": "the system",
            "type": "grammar",
            "note_ru": "Без изменений.",
            "pattern": "other",
        },
        {
            "quote": "project",
            "fix": "projects",
            "type": "spelling",
            "note_ru": "Тип не из перечня.",
            "pattern": "other",
        },
        {
            "quote": "a missing fragment",
            "fix": "x",
            "type": "vocabulary",
            "note_ru": "Нет в тексте.",
            "pattern": "word_choice",
        },
        {
            "quote": "I have worked",
            "fix": "I've been working",
            "type": "phrasing",
            "note_ru": "Длительность.",
            "pattern": "present_perfect_vs_past_simple",
        },
    )
    items, better = postprocess(raw, TEXT, unsure_words=["worked"])
    quotes = [i["quote"] for i in items]
    assert quotes == ["since two years", "we was migrating", "a missing fragment"]
    assert items[1]["pattern"] == "other"  # unknown key replaced
    assert items[2]["start"] is None and items[2]["end"] is None  # stays in the card only
    assert TEXT[items[0]["start"] : items[0]["end"]] == "since two years"
    assert better == "Better."
    assert len(items) <= 3


def test_empty_items_clear_better() -> None:
    raw = parse_response('{"items": [], "better": "Something"}')
    assert raw is not None
    items, better = postprocess(raw, TEXT, [])
    assert items == [] and better == ""


def test_prompt_contains_json_and_all_keys() -> None:
    prompt = build_system_prompt(pattern_keys())
    assert "json" in prompt
    for key in ("for_since", "was_were", "other"):
        assert key in prompt


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_retry_once_on_empty_then_ok(mock_llm: MockLLM, test_key: str) -> None:
    client = LLMClient()
    ep = Endpoint(base_url=mock_llm.base_url, model="m", api_key=test_key)
    mock_llm.script(empty_json_count=1)
    res = await run_corrector(
        client, ep, level="B1", tutor_said="Hi", learner_said=TEXT, unsure_words=[]
    )
    assert res.status == "ok"
    assert len([r for r in mock_llm.requests if r["body"].get("response_format")]) == 2
    await client.aclose()


@pytest.mark.anyio
async def test_two_failures_give_failed(mock_llm: MockLLM, test_key: str) -> None:
    client = LLMClient()
    ep = Endpoint(base_url=mock_llm.base_url, model="m", api_key=test_key)
    mock_llm.script(invalid_json=True)
    res = await run_corrector(
        client, ep, level="B1", tutor_said="Hi", learner_said=TEXT, unsure_words=[]
    )
    assert res.status == "failed"
    assert len(mock_llm.requests) == 2
    await client.aclose()


@pytest.mark.anyio
async def test_unsure_word_never_quoted(mock_llm: MockLLM, test_key: str) -> None:
    client = LLMClient()
    ep = Endpoint(base_url=mock_llm.base_url, model="m", api_key=test_key)
    # The mock quotes the first two words ("I have"); "have" is marked unsure.
    res = await run_corrector(
        client, ep, level="B1", tutor_said="Hi", learner_said=TEXT, unsure_words=["have"]
    )
    assert res.status == "empty"
    await client.aclose()
