"""Exact tokenizer-backed context budgeting for structured LLM microtasks."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from app.core.error_codes import ErrorCode
from app.core.exceptions import LLMProviderError
from app.providers.llm.base import LLMProvider


@dataclass(frozen=True, slots=True)
class TokenBudgetReport:
    context_window: int
    system_tokens: int
    schema_tokens: int
    user_tokens: int
    history_tokens: int
    output_reserve: int
    safety_margin: int

    @property
    def input_tokens(self) -> int:
        return self.system_tokens + self.schema_tokens + self.user_tokens + self.history_tokens

    @property
    def total_reserved(self) -> int:
        return self.input_tokens + self.output_reserve + self.safety_margin

    @property
    def fits(self) -> bool:
        return self.total_reserved <= self.context_window

    def model_dump(self) -> dict[str, int | bool]:
        return {**asdict(self), "input_tokens": self.input_tokens, "total_reserved": self.total_reserved, "fits": self.fits}


class ContextBudgetExceeded(LLMProviderError):
    def __init__(self, report: TokenBudgetReport):
        self.report = report
        super().__init__(
            f"LLM context budget exceeded: {report.total_reserved}>{report.context_window}",
            code=ErrorCode.LLM_TOKEN_LIMIT,
        )


class ContextBudgetManager:
    def __init__(
        self,
        provider: LLMProvider,
        *,
        context_window: int,
        safety_margin: int,
    ) -> None:
        self.provider = provider
        self.context_window = context_window
        self.safety_margin = safety_margin

    async def inspect(
        self,
        *,
        system_prompt: str,
        response_schema: dict[str, Any] | None,
        user_payload: dict[str, Any],
        history: list[dict[str, Any]] | None = None,
        output_reserve: int,
    ) -> TokenBudgetReport:
        schema_text = json.dumps(response_schema or {}, ensure_ascii=False, sort_keys=True)
        user_text = json.dumps(user_payload, ensure_ascii=False, sort_keys=True)
        history_text = json.dumps(history or [], ensure_ascii=False, sort_keys=True)
        counts = []
        for value in (system_prompt, schema_text, user_text, history_text):
            counts.append(await self.provider.count_tokens(value))
        return TokenBudgetReport(
            context_window=self.context_window,
            system_tokens=counts[0],
            schema_tokens=counts[1],
            user_tokens=counts[2],
            history_tokens=counts[3],
            output_reserve=output_reserve,
            safety_margin=self.safety_margin,
        )

    async def ensure_fits(self, **kwargs: Any) -> TokenBudgetReport:
        report = await self.inspect(**kwargs)
        if not report.fits:
            raise ContextBudgetExceeded(report)
        return report
