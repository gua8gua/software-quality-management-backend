import json

import httpx
import pytest

from app.core.error_codes import ErrorCode
from app.core.exceptions import LLMProviderError
from app.providers.llm.openai_compatible import OpenAICompatibleLLMProvider


def _provider(
    transport: httpx.MockTransport | None, *, api_key: str = "test-key"
) -> OpenAICompatibleLLMProvider:
    return OpenAICompatibleLLMProvider(
        base_url="https://example.test/v1",
        api_key=api_key,
        model="test-model",
        timeout_seconds=5.0,
        transport=transport,
    )


def _chat_transport(content: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    return httpx.MockTransport(handler)


async def test_chat_json_parses_plain_json() -> None:
    provider = _provider(_chat_transport('{"rankings": [{"chunk_id": "a", "rerank_score": 0.9}]}'))

    result = await provider.chat_json(system_prompt="sys", user_payload={"query": "q"})

    assert result == {"rankings": [{"chunk_id": "a", "rerank_score": 0.9}]}


async def test_chat_json_strips_markdown_code_fence() -> None:
    fenced = '```json\n{"rankings": []}\n```'
    provider = _provider(_chat_transport(fenced))

    result = await provider.chat_json(system_prompt="sys", user_payload={})

    assert result == {"rankings": []}


async def test_chat_json_extracts_json_from_surrounding_text() -> None:
    provider = _provider(_chat_transport('好的，结果如下：{"rankings": []} 谢谢'))

    result = await provider.chat_json(system_prompt="sys", user_payload={})

    assert result == {"rankings": []}


async def test_chat_json_sends_system_and_user_messages() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = request.read()
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    provider = _provider(httpx.MockTransport(handler))
    await provider.chat_json(
        system_prompt="你是重排器",
        user_payload={"query": "退款", "top_n": 5},
        temperature=0.0,
    )

    sent = json.loads(captured["json"])
    assert sent["model"] == "test-model"
    assert sent["temperature"] == 0.0
    assert sent["messages"][0] == {"role": "system", "content": "你是重排器"}
    assert json.loads(sent["messages"][1]["content"]) == {"query": "退款", "top_n": 5}


async def test_chat_json_invalid_json_raises_llm_provider_error() -> None:
    provider = _provider(_chat_transport("这不是 JSON"))

    with pytest.raises(LLMProviderError) as exc_info:
        await provider.chat_json(system_prompt="sys", user_payload={})

    assert exc_info.value.error_code is ErrorCode.LLM_NO_RESPONSE


async def test_chat_json_http_error_raises_llm_provider_error() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(500, json={"error": "boom"}))
    provider = _provider(transport)

    with pytest.raises(LLMProviderError) as exc_info:
        await provider.chat_json(system_prompt="sys", user_payload={})

    assert exc_info.value.error_code is ErrorCode.LLM_ERROR


async def test_chat_json_rate_limit_maps_to_429_code() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(429, json={"error": "slow down"})
    )
    provider = _provider(transport)

    with pytest.raises(LLMProviderError) as exc_info:
        await provider.chat_json(system_prompt="sys", user_payload={})

    assert exc_info.value.error_code is ErrorCode.LLM_RATE_LIMIT


async def test_chat_json_timeout_raises_llm_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    provider = _provider(httpx.MockTransport(handler))

    with pytest.raises(LLMProviderError) as exc_info:
        await provider.chat_json(system_prompt="sys", user_payload={})

    assert exc_info.value.error_code is ErrorCode.LLM_TIMEOUT


async def test_chat_json_missing_api_key_raises_config_error() -> None:
    provider = _provider(None, api_key="")

    with pytest.raises(LLMProviderError) as exc_info:
        await provider.chat_json(system_prompt="sys", user_payload={})

    assert exc_info.value.error_code is ErrorCode.LLM_CONFIG_ERROR


async def test_chat_json_malformed_response_shape_raises() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"unexpected": True}))
    provider = _provider(transport)

    with pytest.raises(LLMProviderError) as exc_info:
        await provider.chat_json(system_prompt="sys", user_payload={})

    assert exc_info.value.error_code is ErrorCode.LLM_NO_RESPONSE
