"""OpenAI-compatible chat completion 大模型实现。

当前正式路线用它连接本地 vLLM/Ollama 等 OpenAI-compatible 服务；是否启用由
``LLM_ENABLED`` 控制。适配器允许本地服务不设置 API key。

本实现只负责「模型调用 + JSON 解析」，不包含任何业务逻辑；具体业务（重排、改写等）
由依赖本接口的上层 provider 处理。
"""

import json
import time
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

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
        model_version: str = "",
        timeout_seconds: float = 60.0,
        native_json_schema: bool = False,
        chat_template_enable_thinking: bool | None = None,
        tokenize_url: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
        trust_env: bool = True,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        root_url = base_url.rstrip("/")
        if root_url.endswith("/v1"):
            root_url = root_url[:-3]
        self._tokenize_url = tokenize_url or f"{root_url}/tokenize"
        self._api_key = api_key
        self.model = model
        self.model_version = model_version or model
        self._timeout_seconds = timeout_seconds
        self._native_json_schema = native_json_schema
        self._chat_template_enable_thinking = chat_template_enable_thinking
        # transport 仅用于测试注入（httpx.MockTransport），生产环境为 None。
        self._transport = transport
        self._trust_env = trust_env

    async def count_tokens(self, text: str) -> int:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
                trust_env=self._trust_env,
            ) as client:
                response = await client.post(
                    self._tokenize_url,
                    headers=headers,
                    json={"model": self.model, "prompt": text},
                )
                response.raise_for_status()
                body = response.json()
        except httpx.TimeoutException as exc:
            raise LLMProviderError(
                f"tokenizer request timeout: {exc}", code=ErrorCode.LLM_TIMEOUT
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMProviderError(
                f"model tokenizer unavailable: {exc}", code=ErrorCode.LLM_CONFIG_ERROR
            ) from exc
        count = body.get("count")
        if isinstance(count, int):
            return count
        tokens = body.get("tokens")
        if isinstance(tokens, list):
            return len(tokens)
        raise LLMProviderError(
            "tokenizer response contains neither count nor tokens",
            code=ErrorCode.LLM_CONFIG_ERROR,
        )

    async def chat_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict,
        response_schema: dict | None = None,
        temperature: float = 0.0,
        timeout_seconds: int | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        timeout = float(timeout_seconds) if timeout_seconds is not None else self._timeout_seconds
        user_content = json.dumps(user_payload, ensure_ascii=False)

        # 大模型交互日志：记录 prompt 摘要，避免落盘超长内容。
        log_llm_request(self.model, system_prompt, action="chat", payload=user_content)
        started = time.perf_counter()

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_schema is not None and self._native_json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_response",
                    "strict": True,
                    "schema": response_schema,
                },
            }
        if self._chat_template_enable_thinking is not None:
            payload["chat_template_kwargs"] = {
                "enable_thinking": self._chat_template_enable_thinking
            }

        try:
            async with httpx.AsyncClient(
                timeout=timeout, transport=self._transport, trust_env=self._trust_env
            ) as client:
                async for attempt in AsyncRetrying(
                    stop=stop_after_attempt(3),
                    wait=wait_exponential_jitter(initial=0.5, max=4.0),
                    retry=retry_if_exception(_is_transient_http_error),
                    reraise=True,
                ):
                    with attempt:
                        response = await client.post(self._url, headers=headers, json=payload)
                        response.raise_for_status()
                        body = response.json()
        except httpx.TimeoutException as exc:
            self._fail(ErrorCode.LLM_TIMEOUT, f"chat request timeout: {exc}", started, exc)
        except httpx.HTTPStatusError as exc:
            response_text = exc.response.text.lower()
            if exc.response.status_code == 429:
                code = ErrorCode.LLM_RATE_LIMIT
            elif "context" in response_text and any(
                marker in response_text for marker in ("length", "token", "maximum", "overflow")
            ):
                code = ErrorCode.LLM_TOKEN_LIMIT
            else:
                code = ErrorCode.LLM_ERROR
            self._fail(
                code,
                f"chat provider HTTP {exc.response.status_code}: {exc}",
                started,
                exc,
            )
        except (httpx.HTTPError, ValueError) as exc:
            self._fail(ErrorCode.LLM_ERROR, f"chat provider request failed: {exc}", started, exc)

        try:
            choice = body["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            self._fail(
                ErrorCode.LLM_NO_RESPONSE,
                "chat provider returned an invalid response",
                started,
                exc,
            )

        if choice.get("finish_reason") == "length":
            self._fail(
                ErrorCode.LLM_OUTPUT_TRUNCATED,
                "chat provider stopped because the output token limit was reached",
                started,
                None,
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


def _is_transient_http_error(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and (
        exc.response.status_code == 429 or exc.response.status_code >= 500
    )


def _summarize(parsed: dict) -> str:
    """生成用于日志的返回摘要，避免落盘超长 JSON。"""
    rankings = parsed.get("rankings")
    if isinstance(rankings, list):
        return f"<json rankings={len(rankings)}>"
    return f"<json keys={list(parsed.keys())}>"
