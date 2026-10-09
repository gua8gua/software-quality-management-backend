import json

import httpx
import pytest

from app.providers.llm.openai_compatible import OpenAICompatibleLLMProvider


@pytest.mark.asyncio
async def test_local_qwen_can_disable_thinking_through_chat_template_kwargs():
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    provider = OpenAICompatibleLLMProvider(
        base_url="http://local-vllm/v1",
        api_key="",
        model="qwen35-9b",
        native_json_schema=True,
        chat_template_enable_thinking=False,
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )

    result = await provider.chat_json(
        system_prompt="Return JSON",
        user_payload={"value": 1},
        response_schema={"type": "object"},
        max_tokens=512,
    )

    assert result == {"ok": True}
    assert captured["chat_template_kwargs"] == {"enable_thinking": False}
    assert captured["max_tokens"] == 512
    assert captured["response_format"]["type"] == "json_schema"
