from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, document: Document) -> Document:
        self._session.add(document)
        await self._session.flush()
        return document

    async def get_by_id(self, document_id: str) -> Document | None:
        return await self._session.scalar(select(Document).where(Document.id == document_id))

    async def set_status(
        self,
        document: Document,
        status: DocumentStatus,
        *,
        error_message: str | None = None,
    ) -> None:
        document.status = int(status)
        document.error_message = error_message
        await self._session.flush()

