import time
from typing import Any

import httpx

from app.core.error_codes import ErrorCode
from app.core.exceptions import EmbeddingProviderError
from app.core.logging import log_llm_error, log_llm_request, log_llm_response
from app.providers.embedding.base import EmbeddingProvider


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        expected_dimension: int,
        timeout_seconds: float = 30.0,
        trust_env: bool = True,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self._api_key = api_key
        self._model = model
        self._expected_dimension = expected_dimension
        self._timeout_seconds = timeout_seconds
        self._trust_env = trust_env

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self._api_key:
            raise EmbeddingProviderError(
                "MODEL_API_KEY is not configured", code=ErrorCode.LLM_CONFIG_ERROR
            )

        # 大模型交互日志：记录 prompt 摘要，避免落盘海量向量
        log_llm_request(self._model, texts[0], action="embed", count=len(texts))
        started = time.perf_counter()

        headers = {"Authorization": f"Bearer {self._api_key}"}
        payload: dict[str, Any] = {"model": self._model, "input": texts}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds, trust_env=self._trust_env
            ) as client:
                response = await client.post(self._url, headers=headers, json=payload)
                response.raise_for_status()
                body = response.json()
        except httpx.TimeoutException as exc:
            self._fail(ErrorCode.LLM_TIMEOUT, f"embedding request timeout: {exc}", started, exc)
        except httpx.HTTPStatusError as exc:
            code = (
                ErrorCode.LLM_RATE_LIMIT
                if exc.response.status_code == 429
                else ErrorCode.LLM_ERROR
            )
            self._fail(
                code,
                f"embedding provider HTTP {exc.response.status_code}: {exc}",
                started,
                exc,
            )
        except (httpx.HTTPError, ValueError) as exc:
            self._fail(
                ErrorCode.LLM_ERROR, f"embedding provider request failed: {exc}", started, exc
            )

        try:
            ordered = sorted(body["data"], key=lambda item: item["index"])
            vectors = [item["embedding"] for item in ordered]
        except (KeyError, TypeError) as exc:
            self._fail(
                ErrorCode.LLM_NO_RESPONSE,
                "embedding provider returned an invalid response",
                started,
                exc,
            )

        if len(vectors) != len(texts):
            self._fail(
                ErrorCode.LLM_NO_RESPONSE,
                f"expected {len(texts)} vectors, got {len(vectors)}",
                started,
                None,
            )
        if any(len(vector) != self._expected_dimension for vector in vectors):
            self._fail(
                ErrorCode.LLM_ERROR,
                f"embedding dimension must be {self._expected_dimension}",
                started,
                None,
            )

        cost_ms = (time.perf_counter() - started) * 1000
        log_llm_response(
            self._model,
            f"<{len(vectors)} vectors, dim={self._expected_dimension}>",
            cost_ms,
            action="embed",
        )
        return vectors

    def _fail(
        self,
        code: ErrorCode,
        reason: str,
        started: float,
        exc: Exception | None,
    ) -> None:
        """统一记录大模型调用失败日志并抛出标准异常。"""
        cost_ms = (time.perf_counter() - started) * 1000
        log_llm_error(self._model, reason, cost_ms, _stacklevel=4, action="embed", code=code.code)
        if exc is not None:
            raise EmbeddingProviderError(reason, code=code) from exc
        raise EmbeddingProviderError(reason, code=code)
