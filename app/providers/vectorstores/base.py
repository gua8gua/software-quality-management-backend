from abc import ABC, abstractmethod
from typing import Any


class VectorStore(ABC):
    name: str

    @abstractmethod
    async def add_chunks(self, chunks: list[dict[str, Any]]) -> None:
        """Stage chunks and embeddings for persistence."""

    @abstractmethod
    async def similarity_search(
        self,
        query_vector: list[float],
        *,
        tenant_id: str,
        kb_id: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Return chunks ordered from most to least similar."""

    @abstractmethod
    async def delete_by_document_id(self, document_id: str) -> None:
        """Delete all indexed chunks belonging to a document."""

