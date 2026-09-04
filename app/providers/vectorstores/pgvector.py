from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.providers.vectorstores.base import VectorStore
from app.repositories.chunk_repository import ChunkRepository


class PgVectorStore(VectorStore):
    name = "pgvector"

    def __init__(self, session: AsyncSession, repository: ChunkRepository) -> None:
        self._session = session
        self._repository = repository

    async def add_chunks(self, chunks: list[dict[str, Any]]) -> None:
        entities = [Chunk(**chunk) for chunk in chunks]
        await self._repository.add_many(entities)

    async def similarity_search(
        self,
        query_vector: list[float],
        *,
        tenant_id: str,
        kb_id: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        return await self._repository.similarity_search(
            query_vector,
            tenant_id=tenant_id,
            kb_id=kb_id,
            top_k=top_k,
        )

    async def delete_by_document_id(self, document_id: str) -> None:
        await self._repository.delete_by_document_id(document_id)

