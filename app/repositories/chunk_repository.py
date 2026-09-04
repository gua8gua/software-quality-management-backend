from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk


class ChunkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_many(self, chunks: list[Chunk]) -> None:
        self._session.add_all(chunks)
        await self._session.flush()

    async def similarity_search(
        self,
        query_vector: list[float],
        *,
        tenant_id: str,
        kb_id: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        distance = Chunk.embedding.cosine_distance(query_vector)
        statement = (
            select(Chunk, distance.label("distance"))
            .where(Chunk.tenant_id == tenant_id, Chunk.kb_id == kb_id)
            .order_by(distance)
            .limit(top_k)
        )
        rows = (await self._session.execute(statement)).all()
        return [
            {
                "document_id": chunk.document_id,
                "chunk_id": chunk.id,
                "title": chunk.title,
                "content": chunk.content,
                "score": max(-1.0, min(1.0, 1.0 - float(row_distance))),
                "metadata": chunk.chunk_metadata,
            }
            for chunk, row_distance in rows
        ]

    async def delete_by_document_id(self, document_id: str) -> None:
        await self._session.execute(delete(Chunk).where(Chunk.document_id == document_id))

