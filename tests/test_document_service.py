import pytest

from app.core.error_codes import ErrorCode
from app.core.exceptions import IndexingError, KnowledgeBaseNotFoundError
from app.models.document import DocumentStatus
from app.schemas.document import DocumentUpload
from app.services.document_service import DocumentService


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeKnowledgeBases:
    def __init__(self, exists: bool = True) -> None:
        self.exists = exists

    async def get_by_id_and_tenant(self, kb_id: str, tenant_id: str) -> object | None:
        return object() if self.exists else None


class FakeDocuments:
    def __init__(self) -> None:
        self.document = None

    async def add(self, document: object) -> object:
        self.document = document
        return document

    async def get_by_id(self, document_id: str) -> object | None:
        return self.document

    async def set_status(
        self,
        document: object,
        status: DocumentStatus,
        *,
        error_message: str | None = None,
    ) -> None:
        document.status = int(status)
        document.error_message = error_message


class FakeIndexing:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def index_document(self, **_kwargs: object) -> int:
        if self.error:
            raise self.error
        return 2


def upload_request() -> DocumentUpload:
    return DocumentUpload(
        tenant_id="tenant",
        kb_id="00000000-0000-0000-0000-000000000001",
        title="title",
        content="content",
    )


@pytest.mark.asyncio
async def test_upload_sets_success_after_indexing() -> None:
    session = FakeSession()
    documents = FakeDocuments()
    service = DocumentService(
        knowledge_base_repository=FakeKnowledgeBases(),
        document_repository=documents,
        indexing_service=FakeIndexing(),
        session=session,
    )

    result = await service.upload(upload_request())

    assert result.status == int(DocumentStatus.SUCCESS)
    assert result.chunk_count == 2
    assert documents.document.status == int(DocumentStatus.SUCCESS)
    assert session.commits == 2


@pytest.mark.asyncio
async def test_upload_persists_failed_status_when_indexing_fails() -> None:
    session = FakeSession()
    documents = FakeDocuments()
    service = DocumentService(
        knowledge_base_repository=FakeKnowledgeBases(),
        document_repository=documents,
        indexing_service=FakeIndexing(RuntimeError("provider unavailable")),
        session=session,
    )

    # 对外消息简洁统一，内部排查上下文（真实原因）放进 data
    with pytest.raises(IndexingError) as exc_info:
        await service.upload(upload_request())

    error = exc_info.value
    assert error.code == ErrorCode.INDEXING_ERROR.code
    assert error.message == ErrorCode.INDEXING_ERROR.msg
    assert error.data["reason"] == "provider unavailable"

    assert documents.document.status == int(DocumentStatus.FAILED)
    assert documents.document.error_message == "provider unavailable"
    assert session.rollbacks == 1
    assert session.commits == 2


@pytest.mark.asyncio
async def test_upload_rejects_cross_tenant_or_unknown_knowledge_base() -> None:
    service = DocumentService(
        knowledge_base_repository=FakeKnowledgeBases(exists=False),
        document_repository=FakeDocuments(),
        indexing_service=FakeIndexing(),
        session=FakeSession(),
    )
    with pytest.raises(KnowledgeBaseNotFoundError):
        await service.upload(upload_request())
