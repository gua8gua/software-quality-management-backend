from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    name: str = "base"
    model: str = ""
    model_version: str = ""

    async def input_length_report(self, texts: list[str]) -> dict:
        """Return an explicit preflight result when no local tokenizer exists."""
        return {
            "status": "tokenizer_unavailable",
            "model": self.model,
            "count": len(texts),
            "max_tokens": None,
            "oversize_indexes": [],
        }

    async def query_input_length_report(self, texts: list[str], instruction: str) -> dict:
        """Preflight the exact instruction-prefixed strings sent for query embedding."""
        queries = [f"Instruct: {instruction}\nQuery:{value}" for value in texts]
        return await self.input_length_report(queries)

    @abstractmethod
    async def encode_queries(self, texts: list[str], instruction: str) -> list[list[float]]:
        raise NotImplementedError

    @abstractmethod
    async def encode_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError
