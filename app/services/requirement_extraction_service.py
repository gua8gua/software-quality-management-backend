from app.core.exceptions import LLMProviderError
from app.providers.llm.base import LLMProvider
from app.schemas.requirement import (
    RequirementCandidate,
    RequirementExtractionResult,
)

REQUIREMENT_EXTRACTION_PROMPT = """你是软件需求分析专家。
请从用户提供的项目文档片段中提取可独立验证的软件需求。

规则：
1. 只提取功能需求、非功能需求、业务规则和技术约束。
2. 背景、目标、解释、示例和纯设计描述不是需求时不要提取。
3. 一个需求只描述一个独立行为或约束；同一句包含多个独立行为时拆成多条。
4. 前置条件、异常情况和验证方式放入 acceptance_criteria，不要无意义拆分。
5. 不要补充原文没有表达的信息；不确定的优先级使用 unspecified。
6. source_quote 必须是输入片段中的原文，不能改写。
7. 只返回 JSON 对象，不要返回 Markdown 或解释文字。

返回格式：
{
  "requirements": [
    {
      "title": "简短标题",
      "description": "完整、可验证的需求描述",
      "requirement_type": "functional|non_functional|business_rule|constraint",
      "priority": "high|medium|low|unspecified",
      "actor": "执行者，没有则为 null",
      "acceptance_criteria": ["验收条件"],
      "source_quote": "原文引用",
      "source_location": "来源位置，没有则为 null",
      "confidence": 0.0
    }
  ]
}
"""


class RequirementExtractionService:
    def __init__(self, llm_provider: LLMProvider) -> None:
        self._llm_provider = llm_provider

    async def extract(
        self,
        *,
        document_id: str,
        content: str,
        source_location: str | None = None,
    ) -> RequirementExtractionResult:
        result = await self._llm_provider.chat_json(
            system_prompt=REQUIREMENT_EXTRACTION_PROMPT,
            user_payload={
                "document_id": document_id,
                "source_location": source_location,
                "content": content,
            },
            temperature=0.0,
        )
        try:
            extracted = RequirementExtractionResult.model_validate(result)
        except ValueError as exc:
            raise LLMProviderError(
                f"requirement extraction returned invalid data: {exc}"
            ) from exc

        normalized = [
            RequirementCandidate(
                **requirement.model_dump(exclude={"source_location"}),
                source_location=requirement.source_location or source_location,
            )
            for requirement in extracted.requirements
        ]
        return RequirementExtractionResult(requirements=normalized)
