import asyncio
from typing import Any

import httpx

from app.providers.embeddings.base import EmbeddingProvider


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """Thin adapter for an OpenAI-compatible embedding endpoint.

    vLLM's ``/tokenize`` endpoint is used before inference so a remote provider
    cannot silently truncate an oversized retrieval projection.
    """

    name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        model_version: str = "",
        batch_size: int = 8,
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        trust_env: bool = True,
    ) -> None:
        normalized = base_url.rstrip("/")
        self._embedding_url = f"{normalized}/embeddings"
        tokenizer_root = normalized[:-3] if normalized.endswith("/v1") else normalized
        self._tokenize_url = f"{tokenizer_root}/tokenize"
        self._api_key = api_key
        self.model = model
        self.model_version = model_version or model
        self.batch_size = batch_size
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._trust_env = trust_env

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds,
            transport=self._transport,
            trust_env=self._trust_env,
        ) as client:
            response = await client.post(url, headers=self._headers, json=payload)
            response.raise_for_status()
            body = response.json()
        if not isinstance(body, dict):
            raise ValueError("embedding provider returned a non-object response")
        return body

    async def input_length_report(self, texts: list[str]) -> dict:
        if not texts:
            return {
                "status": "ok",
                "model": self.model,
                "count": 0,
                "max_tokens": None,
                "observed_max_tokens": 0,
                "oversize_indexes": [],
                "token_lengths": [],
            }

        semaphore = asyncio.Semaphore(self.batch_size)

        async def tokenize(text: str) -> tuple[int, int]:
            async with semaphore:
                body = await self._post(
                    self._tokenize_url,
                    {"model": self.model, "prompt": text, "add_special_tokens": True},
                )
            return int(body["count"]), int(body["max_model_len"])

        values = await asyncio.gather(*(tokenize(text) for text in texts))
        lengths = [value[0] for value in values]
        limits = {value[1] for value in values}
        if len(limits) != 1:
            raise ValueError("embedding tokenizer returned inconsistent model limits")
        limit = limits.pop()
        oversize = [index for index, length in enumerate(lengths) if length > limit]
        return {
            "status": "oversize" if oversize else "ok",
            "model": self.model,
            "count": len(texts),
            "max_tokens": limit,
            "observed_max_tokens": max(lengths),
            "oversize_indexes": oversize,
            "token_lengths": lengths,
        }

    async def _encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for offset in range(0, len(texts), self.batch_size):
            body = await self._post(
                self._embedding_url,
                {
                    "model": self.model,
                    "input": texts[offset : offset + self.batch_size],
                    "encoding_format": "float",
                },
            )
            data = body.get("data")
            if not isinstance(data, list):
                raise ValueError("embedding provider response does not contain a data array")
            ordered = sorted(data, key=lambda item: int(item["index"]))
            vectors.extend([list(map(float, item["embedding"])) for item in ordered])
        if len(vectors) != len(texts):
            raise ValueError("embedding provider returned a different number of vectors")
        return vectors

    async def encode_queries(self, texts: list[str], instruction: str) -> list[list[float]]:
        queries = [f"Instruct: {instruction}\nQuery:{value}" for value in texts]
        return await self._encode(queries)

    async def encode_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._encode(texts)
