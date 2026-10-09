import math
from typing import Any

import httpx

from app.providers.embeddings.base import EmbeddingProvider


class LocalEmbeddingUnavailableError(RuntimeError):
    """The configured local Ollama embedding service cannot be reached."""


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Thin adapter for Ollama's native embedding API.

    Ollama does not expose a tokenizer preflight endpoint. Every request sets
    ``truncate`` to false so an oversized canonical projection fails visibly
    instead of being silently shortened.
    """

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        model_version: str = "",
        batch_size: int = 8,
        timeout_seconds: float = 60.0,
        api_key: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
        trust_env: bool = True,
    ) -> None:
        normalized = base_url.rstrip("/")
        if normalized.endswith("/api/embed"):
            self._embedding_url = normalized
        else:
            if normalized.endswith("/v1"):
                normalized = normalized[:-3]
            if normalized.endswith("/api"):
                normalized = normalized[:-4]
            self._embedding_url = f"{normalized}/api/embed"
        self.model = model
        self.model_version = model_version or model
        self.batch_size = batch_size
        self._timeout_seconds = timeout_seconds
        self._api_key = api_key
        self._transport = transport
        self._trust_env = trust_env

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def input_length_report(self, texts: list[str]) -> dict:
        return {
            "status": "provider_enforced_no_truncation",
            "model": self.model,
            "count": len(texts),
            "max_tokens": None,
            "observed_max_tokens": None,
            "oversize_indexes": [],
            "token_lengths": [],
        }

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
                trust_env=self._trust_env,
            ) as client:
                response = await client.post(
                    self._embedding_url,
                    headers=self._headers,
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except httpx.RequestError as exc:
            raise LocalEmbeddingUnavailableError(
                "LOCAL_EMBEDDING_UNAVAILABLE: local Ollama request failed"
            ) from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                raise LocalEmbeddingUnavailableError(
                    "LOCAL_EMBEDDING_UNAVAILABLE: local Ollama service is unhealthy"
                ) from exc
            raise
        if not isinstance(body, dict):
            raise ValueError("Ollama embedding provider returned a non-object response")
        return body

    @staticmethod
    def _validated_vectors(body: dict[str, Any], expected_count: int) -> list[list[float]]:
        raw_vectors = body.get("embeddings")
        if not isinstance(raw_vectors, list):
            raise ValueError("Ollama embedding response does not contain an embeddings array")
        if len(raw_vectors) != expected_count:
            raise ValueError("Ollama embedding provider returned a different number of vectors")

        vectors: list[list[float]] = []
        dimensions: set[int] = set()
        for raw_vector in raw_vectors:
            if not isinstance(raw_vector, list) or not raw_vector:
                raise ValueError("Ollama embedding provider returned an empty or invalid vector")
            try:
                vector = [float(value) for value in raw_vector]
            except (TypeError, ValueError) as exc:
                raise ValueError("Ollama embedding provider returned a non-numeric vector") from exc
            if not all(math.isfinite(value) for value in vector):
                raise ValueError("Ollama embedding provider returned a non-finite vector")
            dimensions.add(len(vector))
            vectors.append(vector)
        if len(dimensions) != 1:
            raise ValueError("Ollama embedding provider returned inconsistent vector dimensions")
        return vectors

    async def _encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        expected_dimension: int | None = None
        for offset in range(0, len(texts), self.batch_size):
            batch = texts[offset : offset + self.batch_size]
            body = await self._post(
                {
                    "model": self.model,
                    "input": batch,
                    "truncate": False,
                }
            )
            batch_vectors = self._validated_vectors(body, len(batch))
            dimension = len(batch_vectors[0])
            if expected_dimension is not None and dimension != expected_dimension:
                raise ValueError("Ollama embedding dimension changed between batches")
            expected_dimension = dimension
            vectors.extend(batch_vectors)
        return vectors

    async def encode_queries(self, texts: list[str], instruction: str) -> list[list[float]]:
        queries = [f"Instruct: {instruction}\nQuery:{value}" for value in texts]
        return await self._encode(queries)

    async def encode_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._encode(texts)
