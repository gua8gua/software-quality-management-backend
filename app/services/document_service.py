from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import IndexingError, KnowledgeBaseNotFoundError
from app.models.document import Document, DocumentStatus
from app.repositories.document_repository import DocumentRepository
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.schemas.document import DocumentUpload, DocumentUploaded
from app.services.indexing_service import IndexingService
from app.utils.id_generator import generate_document_id


class DocumentService:
    def __init__(
        self,
        *,
        knowledge_base_repository: KnowledgeBaseRepository,
        document_repository: DocumentRepository,
        indexing_service: IndexingService,
        session: AsyncSession,
    ) -> None:
        self._knowledge_bases = knowledge_base_repository
        self._documents = document_repository
        self._indexing = indexing_service
        self._session = session

    async def upload(self, request: DocumentUpload) -> DocumentUploaded:
        knowledge_base = await self._knowledge_bases.get_by_id_and_tenant(
            request.kb_id, request.tenant_id
        )
        if knowledge_base is None:
            raise KnowledgeBaseNotFoundError()

        document = Document(
            id=generate_document_id(),
            tenant_id=request.tenant_id,
            kb_id=request.kb_id,
            title=request.title,
            source_type="text",
            status=int(DocumentStatus.PROCESSING),
        )
        await self._documents.add(document)
        await self._session.commit()

        try:
            chunk_count = await self._indexing.index_document(
                document_id=document.id,
                tenant_id=document.tenant_id,
                kb_id=document.kb_id,
                title=document.title,
                content=request.content,
                source_type=document.source_type,
            )
            await self._documents.set_status(document, DocumentStatus.SUCCESS)
            await self._session.commit()
        except Exception as exc:
            await self._session.rollback()
            persisted = await self._documents.get_by_id(document.id)
            if persisted is not None:
                error_message = str(exc)[:2000] or exc.__class__.__name__
                await self._documents.set_status(
                    persisted,
                    DocumentStatus.FAILED,
                    error_message=error_message,
                )
                await self._session.commit()
            raise IndexingError(
                str(exc) or exc.__class__.__name__, document_id=document.id
            ) from exc

        return DocumentUploaded(
            document_id=document.id,
            kb_id=document.kb_id,
            status=int(DocumentStatus.SUCCESS),
            chunk_count=chunk_count,
        )
