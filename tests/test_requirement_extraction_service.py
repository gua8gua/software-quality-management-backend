import pytest

from app.core.exceptions import LLMProviderError
from app.providers.llm.base import LLMProvider
from app.schemas.requirement import RequirementExtractionResult
from app.services.requirement_extraction_service import RequirementExtractionService


class StubLLMProvider(LLMProvider):
    model = "stub-model"

    def __init__(self, response: dict) -> None:
        self.response = response
        self.payload: dict | None = None

    async def chat_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict,
        temperature: float = 0.0,
        timeout_seconds: int | None = None,
    ) -> dict:
        self.payload = user_payload
        assert "一个需求只描述一个独立行为或约束" in system_prompt
        assert temperature == 0.0
        return self.response


@pytest.mark.asyncio
async def test_extract_validates_and_inherits_source_location() -> None:
    provider = StubLLMProvider(
        {
            "requirements": [
                {
                    "title": "用户登录",
                    "description": "系统应允许用户使用账号和密码登录。",
                    "requirement_type": "functional",
                    "priority": "high",
                    "actor": "用户",
                    "acceptance_criteria": ["正确凭据可以登录"],
                    "source_quote": "用户可以使用账号和密码登录。",
                    "confidence": 0.9,
                }
            ]
        }
    )

    result = await RequirementExtractionService(provider).extract(
        document_id="doc-1",
        content="用户可以使用账号和密码登录。",
        source_location="第 2 章",
    )

    assert isinstance(result, RequirementExtractionResult)
    assert result.requirements[0].source_location == "第 2 章"
    assert provider.payload == {
        "document_id": "doc-1",
        "source_location": "第 2 章",
        "content": "用户可以使用账号和密码登录。",
    }


@pytest.mark.asyncio
async def test_extract_rejects_invalid_model_output() -> None:
    provider = StubLLMProvider({"requirements": [{"title": "缺少字段"}]})

    with pytest.raises(LLMProviderError) as exc_info:
        await RequirementExtractionService(provider).extract(
            document_id="doc-1",
            content="文档内容",
        )

    assert "requirement extraction returned invalid data" in exc_info.value.data["reason"]
