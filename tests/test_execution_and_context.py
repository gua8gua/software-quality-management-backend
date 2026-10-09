import json

import httpx
import pytest

from app.core.context_budget import ContextBudgetExceeded, ContextBudgetManager
from app.core.error_codes import ErrorCode
from app.core.exceptions import LLMProviderError
from app.core.execution import (
    ErrorCategory,
    classify_exception,
    classify_legacy_fine_result,
    fingerprint_payload,
)
from app.providers.llm.base import LLMProvider
from app.providers.llm.openai_compatible import OpenAICompatibleLLMProvider


class ExactFakeLLM(LLMProvider):
    name = "exact-fake"
    model = "exact-fake"

    async def chat_json(self, **kwargs):
        return kwargs

    async def count_tokens(self, text: str) -> int:
        return len(text.split())


@pytest.mark.asyncio
async def test_context_budget_counts_every_component_and_rejects_overflow():
    manager = ContextBudgetManager(ExactFakeLLM(), context_window=12, safety_margin=2)
    report = await manager.inspect(
        system_prompt="one two",
        response_schema={"type": "object"},
        user_payload={"text": "three four"},
        history=[{"value": "five six"}],
        output_reserve=2,
    )
    assert report.system_tokens == 2
    assert report.schema_tokens > 0
    assert report.user_tokens > 0
    assert report.history_tokens > 0
    assert not report.fits
    with pytest.raises(ContextBudgetExceeded):
        await manager.ensure_fits(
            system_prompt="one two",
            response_schema={"type": "object"},
            user_payload={"text": "three four"},
            history=[{"value": "five six"}],
            output_reserve=2,
        )


def test_error_classification_and_fingerprint_are_stable():
    error = LLMProviderError("too long", code=ErrorCode.LLM_TOKEN_LIMIT)
    assert classify_exception(error).category == ErrorCategory.CONTEXT_OVERFLOW
    assert fingerprint_payload({"b": 2, "a": 1}) == fingerprint_payload({"a": 1, "b": 2})


def test_historical_fine_errors_are_classified_without_changing_semantic_value():
    structured = classify_legacy_fine_result(
        "uncertain", {"validation": ["llm_invalid_structured_output"]}
    )
    semantic = classify_legacy_fine_result("uncertain", {"validation": []})
    assert structured["execution_status"] == "failed"
    assert structured["validation_status"] == "invalid"
    assert structured["error_category"] == "structured_output"
    assert semantic["execution_status"] == "succeeded"
    assert semantic["validation_status"] == "valid"


@pytest.mark.asyncio
async def test_openai_provider_uses_serving_tokenizer_and_detects_truncation():
    requests: list[tuple[str, dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append((request.url.path, payload))
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"count": 7})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "length", "message": {"content": '{"ok": true}'}}
                ]
            },
        )

    provider = OpenAICompatibleLLMProvider(
        base_url="http://local-vllm/v1",
        api_key="",
        model="local",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )
    assert await provider.count_tokens("text") == 7
    with pytest.raises(LLMProviderError) as caught:
        await provider.chat_json(system_prompt="s", user_payload={}, max_tokens=1)
    assert caught.value.code == ErrorCode.LLM_OUTPUT_TRUNCATED.code
    assert [item[0] for item in requests] == ["/tokenize", "/v1/chat/completions"]
