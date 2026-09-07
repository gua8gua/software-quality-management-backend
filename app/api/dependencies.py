from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.providers.documents.base import DocumentParser
from app.providers.documents.text import PlainTextDocumentParser
from app.providers.embedding.base import EmbeddingProvider
from app.providers.embedding.openai_compatible import OpenAICompatibleEmbeddingProvider
from app.providers.llm.base import LLMProvider
from app.providers.llm.openai_compatible import OpenAICompatibleLLMProvider
from app.providers.rerank.base import RerankProvider
from app.providers.rerank.llm import LLMRerankProvider
from app.providers.rerank.noop import NoopRerankProvider
from app.providers.vectorstores.base import VectorStore
from app.providers.vectorstores.pgvector import PgVectorStore
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.retrieval_log_repository import RetrievalLogRepository
from app.services.document_service import DocumentService
from app.services.indexing_service import IndexingService
from app.services.knowledge_base_service import KnowledgeBaseService
from app.services.rag_service import RagService
from app.utils.text_splitter import CharacterTextSplitter, TextSplitter

SessionDep = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_embedding_provider(settings: SettingsDep) -> EmbeddingProvider:
    return OpenAICompatibleEmbeddingProvider(
        base_url=settings.model_base_url,
        api_key=settings.model_api_key,
        model=settings.embedding_model,
        expected_dimension=settings.embedding_dimension,
        timeout_seconds=settings.model_timeout_seconds,
    )


def get_llm_provider(settings: SettingsDep) -> LLMProvider:
    """通用大模型 provider。第一版支持 openai_compatible（DeepSeek / 百炼等）。"""
    if settings.llm_provider != "openai_compatible":
        raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}")
    return OpenAICompatibleLLMProvider(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def get_rerank_provider(
    settings: SettingsDep,
    llm_provider: Annotated[LLMProvider, Depends(get_llm_provider)],
) -> RerankProvider:
    """重排 provider。``llm`` 走通用 LLMProvider 打分，``noop`` 不重排。"""
    if settings.rerank_provider == "noop":
        return NoopRerankProvider()
    if settings.rerank_provider == "llm":
        return LLMRerankProvider(
            llm_provider=llm_provider,
            max_candidates=settings.rerank_max_candidates,
            chunk_max_chars=settings.rerank_chunk_max_chars,
            temperature=settings.rerank_temperature,
            timeout_seconds=int(settings.llm_timeout_seconds),
        )
    raise ValueError(f"Unsupported RERANK_PROVIDER: {settings.rerank_provider}")


def get_document_parser() -> DocumentParser:
    return PlainTextDocumentParser()


def get_text_splitter(settings: SettingsDep) -> TextSplitter:
    return CharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )


def get_vector_store(session: SessionDep, settings: SettingsDep) -> VectorStore:
    if settings.vector_store != "pgvector":
        raise ValueError(f"Unsupported VECTOR_STORE: {settings.vector_store}")
    return PgVectorStore(session, ChunkRepository(session))


def get_knowledge_base_service(session: SessionDep) -> KnowledgeBaseService:
    return KnowledgeBaseService(KnowledgeBaseRepository(session), session)


def get_document_service(
    session: SessionDep,
    parser: Annotated[DocumentParser, Depends(get_document_parser)],
    splitter: Annotated[TextSplitter, Depends(get_text_splitter)],
    embedding_provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
    vector_store: Annotated[VectorStore, Depends(get_vector_store)],
) -> DocumentService:
    document_repository = DocumentRepository(session)
    indexing_service = IndexingService(
        parser=parser,
        splitter=splitter,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )
    return DocumentService(
        knowledge_base_repository=KnowledgeBaseRepository(session),
        document_repository=document_repository,
        indexing_service=indexing_service,
        session=session,
    )


def get_rag_service(
    session: SessionDep,
    settings: SettingsDep,
    embedding_provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
    vector_store: Annotated[VectorStore, Depends(get_vector_store)],
    rerank_provider: Annotated[RerankProvider, Depends(get_rerank_provider)],
) -> RagService:
    return RagService(
        knowledge_base_repository=KnowledgeBaseRepository(session),
        retrieval_log_repository=RetrievalLogRepository(session),
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        session=session,
        top_k=settings.top_k,
        rerank_provider=rerank_provider,
        rerank_enabled=settings.rerank_enabled,
        rerank_top_n=settings.rerank_top_n,
    )

