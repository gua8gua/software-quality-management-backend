"""OpenAI-compatible chat completion 大模型实现。

DeepSeek、阿里云百炼（DashScope OpenAI-compatible 模式）、OpenAI 官方以及 Ollama
等兼容 OpenAI 协议的模型都走这里，只需替换 ``base_url`` / ``model`` / ``api_key``。

本实现只负责「模型调用 + JSON 解析」，不包含任何业务逻辑；具体业务（重排、改写等）
由依赖本接口的上层 provider 处理。
"""

import json
import time
from typing import Any

import httpx

from app.core.error_codes import ErrorCode
from app.core.exceptions import LLMProviderError
from app.core.logging import log_llm_error, log_llm_request, log_llm_response
from app.providers.llm.base import LLMProvider


class OpenAICompatibleLLMProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        trust_env: bool = True,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self.model = model
        self._timeout_seconds = timeout_seconds
        # transport 仅用于测试注入（httpx.MockTransport），生产环境为 None。
        self._transport = transport
        self._trust_env = trust_env

    async def chat_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict,
        temperature: float = 0.0,
        timeout_seconds: int | None = None,
    ) -> dict:
        if not self._api_key:
            raise LLMProviderError(
                "LLM_API_KEY is not configured", code=ErrorCode.LLM_CONFIG_ERROR
            )

        timeout = float(timeout_seconds) if timeout_seconds is not None else self._timeout_seconds
        user_content = json.dumps(user_payload, ensure_ascii=False)

        # 大模型交互日志：记录 prompt 摘要，避免落盘超长内容。
        log_llm_request(self.model, system_prompt, action="chat", payload=user_content)
        started = time.perf_counter()

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }

        try:
            async with httpx.AsyncClient(
                timeout=timeout, transport=self._transport, trust_env=self._trust_env
            ) as client:
                response = await client.post(self._url, headers=headers, json=payload)
                response.raise_for_status()
                body = response.json()
        except httpx.TimeoutException as exc:
            self._fail(ErrorCode.LLM_TIMEOUT, f"chat request timeout: {exc}", started, exc)
        except httpx.HTTPStatusError as exc:
            code = (
                ErrorCode.LLM_RATE_LIMIT
                if exc.response.status_code == 429
                else ErrorCode.LLM_ERROR
            )
            self._fail(
                code,
                f"chat provider HTTP {exc.response.status_code}: {exc}",
                started,
                exc,
            )
        except (httpx.HTTPError, ValueError) as exc:
            self._fail(ErrorCode.LLM_ERROR, f"chat provider request failed: {exc}", started, exc)

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            self._fail(
                ErrorCode.LLM_NO_RESPONSE,
                "chat provider returned an invalid response",
                started,
                exc,
            )

        try:
            parsed = _extract_json_object(content)
        except ValueError as exc:
            self._fail(
                ErrorCode.LLM_NO_RESPONSE,
                f"chat provider did not return valid JSON: {exc}",
                started,
                exc,
            )

        cost_ms = (time.perf_counter() - started) * 1000
        log_llm_response(self.model, _summarize(parsed), cost_ms, action="chat")
        return parsed

    def _fail(
        self,
        code: ErrorCode,
        reason: str,
        started: float,
        exc: Exception | None,
    ) -> None:
        """统一记录大模型调用失败日志并抛出标准异常。"""
        cost_ms = (time.perf_counter() - started) * 1000
        log_llm_error(self.model, reason, cost_ms, _stacklevel=4, action="chat", code=code.code)
        if exc is not None:
            raise LLMProviderError(reason, code=code) from exc
        raise LLMProviderError(reason, code=code)


def _extract_json_object(content: Any) -> dict:
    """从大模型返回内容中提取 JSON 对象。

    兼容三类常见返回：纯 JSON、被 ```` ``` ```` / ```` ```json ```` 包裹、以及 JSON
    前后夹带少量解释性文字。提取失败时抛 ``ValueError``。
    """
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        raise ValueError(f"unexpected content type: {type(content)!r}")

    text = content.strip()
    # 去除 Markdown 代码围栏。
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    # 兜底：截取第一个 `{` 到最后一个 `}` 之间的子串。
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    candidate = text[start : end + 1]

    parsed = json.loads(candidate)  # 解析失败抛 json.JSONDecodeError（ValueError 子类）
    if not isinstance(parsed, dict):
        raise ValueError("model output is not a JSON object")
    return parsed


def _summarize(parsed: dict) -> str:
    """生成用于日志的返回摘要，避免落盘超长 JSON。"""
    rankings = parsed.get("rankings")
    if isinstance(rankings, list):
        return f"<json rankings={len(rankings)}>"
    return f"<json keys={list(parsed.keys())}>"
