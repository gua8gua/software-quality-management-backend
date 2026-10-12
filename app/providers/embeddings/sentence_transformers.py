import asyncio
from typing import Any

from app.providers.embeddings.base import EmbeddingProvider


class SentenceTransformerEmbeddingProvider(EmbeddingProvider):
    """Qwen3 embedding through its officially documented SentenceTransformers path."""

    name = "sentence_transformers"

    def __init__(
        self,
        model: str,
        *,
        revision: str = "main",
        device: str | None = None,
        batch_size: int = 8,
    ):
        self.model = model
        self.model_version = revision
        self.revision = revision
        self.device = device
        self.batch_size = batch_size
        self._encoder: Any | None = None

    def _load(self):
        if self._encoder is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover - depends on deployment extra
                raise RuntimeError(
                    "sentence-transformers is not installed; install the 'models' extra"
                ) from exc
            kwargs = {"device": self.device} if self.device else {}
            self._encoder = SentenceTransformer(self.model, revision=self.revision, **kwargs)
        return self._encoder

    def _encode(self, texts: list[str], *, prompt: str | None = None) -> list[list[float]]:
        if not texts:
            return []
        values = self._load().encode(
            texts,
            prompt=prompt,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return values.tolist()

    def _input_length_report(self, texts: list[str]) -> dict:
        encoder = self._load()
        tokenizer = getattr(encoder, "tokenizer", None)
        limit = int(getattr(encoder, "max_seq_length", 0) or 0)
        if tokenizer is None or not limit:
            return {
                "status": "tokenizer_unavailable",
                "model": self.model,
                "count": len(texts),
                "max_tokens": limit or None,
                "oversize_indexes": [],
            }
        lengths = [
            len(ids)
            for ids in tokenizer(
                texts,
                add_special_tokens=True,
                truncation=False,
                padding=False,
            )["input_ids"]
        ]
        oversize = [index for index, length in enumerate(lengths) if length > limit]
        return {
            "status": "oversize" if oversize else "ok",
            "model": self.model,
            "count": len(texts),
            "max_tokens": limit,
            "observed_max_tokens": max(lengths, default=0),
            "oversize_indexes": oversize,
            "token_lengths": lengths,
        }

    async def input_length_report(self, texts: list[str]) -> dict:
        return await asyncio.to_thread(self._input_length_report, texts)

    async def encode_queries(self, texts: list[str], instruction: str) -> list[list[float]]:
        prompt = f"Instruct: {instruction}\nQuery:"
        return await asyncio.to_thread(self._encode, texts, prompt=prompt)

    async def encode_documents(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._encode, texts)
