from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_base import KnowledgeBase


class KnowledgeBaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, knowledge_base: KnowledgeBase) -> KnowledgeBase:
        self._session.add(knowledge_base)
        await self._session.flush()
        return knowledge_base

    async def get_by_id_and_tenant(self, kb_id: str, tenant_id: str) -> KnowledgeBase | None:
        statement = select(KnowledgeBase).where(
            KnowledgeBase.id == kb_id,
            KnowledgeBase.tenant_id == tenant_id,
        )
        return await self._session.scalar(statement)

