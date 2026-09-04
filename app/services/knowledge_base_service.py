from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_base import KnowledgeBase
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseCreated
from app.utils.id_generator import generate_kb_id


class KnowledgeBaseService:
    def __init__(self, repository: KnowledgeBaseRepository, session: AsyncSession) -> None:
        self._repository = repository
        self._session = session

    async def create(self, request: KnowledgeBaseCreate) -> KnowledgeBaseCreated:
        entity = KnowledgeBase(
            id=generate_kb_id(),
            tenant_id=request.tenant_id,
            name=request.name,
            description=request.description,
        )
        await self._repository.add(entity)
        await self._session.commit()
        await self._session.refresh(entity)
        return KnowledgeBaseCreated(
            kb_id=entity.id,
            name=entity.name,
            tenant_id=entity.tenant_id,
            created_at=entity.created_at,
        )

