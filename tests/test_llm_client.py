"""LLM client against the mock server: stream, errors, timeouts, extra params (plan item 1.4)."""

from __future__ import annotations

import pytest

from tests.mock_llm import MockLLM
from trainer.llm.client import Endpoint, LLMClient, LLMError, Timeouts, validate_base_url

pytestmark = pytest.mark.anyio

MESSAGES = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Hello"}]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def ep(mock: MockLLM, key: str, **kw) -> Endpoint:
    return Endpoint(base_url=mock.base_url, model="mock-model", api_key=key, **kw)


async def collect(client: LLMClient, endpoint: Endpoint) -> str:
    return "".join([part async for part in client.stream_chat(endpoint, MESSAGES)])


async def test_stream_and_extra_params(mock_llm: MockLLM, test_key: str) -> None:
    client = LLMClient()
    mock_llm.script(text="One two three. Four five six seven.", chunk_delay_ms=1)
    endpoint = ep(
        mock_llm, test_key, extra_params={"thinking": {"type": "disabled"}, "temperature": 0.7}
    )
    text = await collect(client, endpoint)
    assert text == "One two three. Four five six seven."
    body = mock_llm.requests[-1]["body"]
    assert body["stream"] is True
    assert body["model"] == "mock-model"
    assert body["thinking"] == {"type": "disabled"}
    assert body["temperature"] == 0.7
    assert body["messages"] == MESSAGES
    assert mock_llm.requests[-1]["authorization"] == f"Bearer {test_key}"
    await client.aclose()


async def test_401(mock_llm: MockLLM, test_key: str) -> None:
    client = LLMClient()
    with pytest.raises(LLMError) as info:
        await collect(client, ep(mock_llm, test_key + "x"))
    assert info.value.code == "llm_unauthorized"
    assert info.value.status == 401
    assert not info.value.retryable
    await client.aclose()


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (429, "llm_rate_limited"),
        (500, "llm_bad_response"),
        (402, "llm_unauthorized"),
        (403, "llm_unauthorized"),
        (503, "llm_bad_response"),
    ],
)
async def test_status_mapping(mock_llm: MockLLM, test_key: str, status: int, code: str) -> None:
    client = LLMClient()
    mock_llm.script(error_status=status)
    with pytest.raises(LLMError) as info:
        await collect(client, ep(mock_llm, test_key))
    assert info.value.code == code
    with pytest.raises(LLMError) as info2:
        await client.complete(ep(mock_llm, test_key), MESSAGES)
    assert info2.value.code == code
    await client.aclose()


async def test_timeout(mock_llm: MockLLM, test_key: str) -> None:
    client = LLMClient(Timeouts(connect=2, read=0.5, total=5))
    mock_llm.script(hang=True)
    with pytest.raises(LLMError) as info:
        await collect(client, ep(mock_llm, test_key))
    assert info.value.code == "llm_unreachable"
    assert info.value.retryable
    await client.aclose()


async def test_broken_stream(mock_llm: MockLLM, test_key: str) -> None:
    client = LLMClient()
    mock_llm.script(text="a b c d e f g h", break_after_chunks=2, chunk_delay_ms=1)
    parts: list[str] = []
    with pytest.raises(LLMError) as info:
        async for p in client.stream_chat(ep(mock_llm, test_key), MESSAGES):
            parts.append(p)
    assert info.value.code == "llm_bad_response"
    assert parts  # the beginning arrived before the stream broke
    await client.aclose()


async def test_unreachable_host(test_key: str) -> None:
    client = LLMClient(Timeouts(connect=1, read=1, total=3))
    endpoint = Endpoint(base_url="http://127.0.0.1:9/v1", model="m", api_key=test_key)
    with pytest.raises(LLMError) as info:
        await collect(client, endpoint)
    assert info.value.code == "llm_unreachable"
    await client.aclose()


async def test_complete_json_mode(mock_llm: MockLLM, test_key: str) -> None:
    client = LLMClient()
    text = await client.complete(ep(mock_llm, test_key), MESSAGES, json_mode=True)
    assert text.startswith("{")
    assert mock_llm.requests[-1]["body"]["response_format"] == {"type": "json_object"}
    await client.complete(ep(mock_llm, test_key, json_mode=False), MESSAGES, json_mode=True)
    assert "response_format" not in mock_llm.requests[-1]["body"]
    await client.aclose()


async def test_error_detail_redacts_key(mock_llm: MockLLM, test_key: str) -> None:
    from trainer.logging_setup import register_secret

    register_secret(test_key)
    client = LLMClient()
    mock_llm.script(error_status=500)
    with pytest.raises(LLMError) as info:
        await client.complete(ep(mock_llm, test_key), MESSAGES)
    assert test_key not in info.value.detail
    assert len(info.value.detail) <= 200
    await client.aclose()


def test_base_url_rules() -> None:
    assert validate_base_url("https://api.deepseek.com")
    assert validate_base_url("http://127.0.0.1:8000/v1")
    assert validate_base_url("http://localhost:1234")
    assert not validate_base_url("http://api.deepseek.com")
    assert not validate_base_url("https://api.deepseek.com?x=1")
