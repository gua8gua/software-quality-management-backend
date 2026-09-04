import logging
from time import perf_counter
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import KnowledgeBaseNotFoundError
from app.core.logging import get_logger, log_event
from app.models.retrieval_log import RetrievalLog
from app.providers.embedding.base import EmbeddingProvider
from app.providers.rerank.base import RerankProvider
from app.providers.rerank.noop import NoopRerankProvider
from app.providers.vectorstores.base import VectorStore
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.retrieval_log_repository import RetrievalLogRepository
from app.schemas.rag import (
    RagRetrieveRequest,
    RagRetrieveResult,
    RetrievalMetadata,
    RetrievedChunk,
)
from app.schemas.rerank import RerankMetadata
from app.utils.id_generator import generate_retrieval_log_id

logger = get_logger(__name__)


def _error_reason(exc: Exception) -> str:
    """提取异常的具体原因。

    :class:`~app.core.exceptions.AppError` 把对外提示放在 ``message``，把排查所需的
    具体原因放在 ``data["reason"]``；重排降级元信息需要后者。
    """
    data = getattr(exc, "data", None)
    if isinstance(data, dict) and data.get("reason"):
        return str(data["reason"])
    return str(exc)


class RagService:
    def __init__(
        self,
        *,
        knowledge_base_repository: KnowledgeBaseRepository,
        retrieval_log_repository: RetrievalLogRepository,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        session: AsyncSession,
        top_k: int,
        rerank_provider: RerankProvider | None = None,
        rerank_enabled: bool = False,
        rerank_top_n: int = 5,
    ) -> None:
        self._knowledge_bases = knowledge_base_repository
        self._retrieval_logs = retrieval_log_repository
        self._embedding_provider = embedding_provider
        self._vector_store = vector_store
        self._session = session
        self._top_k = top_k
        self._rerank_provider = rerank_provider or NoopRerankProvider()
        self._rerank_enabled = rerank_enabled
        self._rerank_top_n = rerank_top_n

    async def retrieve(self, request: RagRetrieveRequest) -> RagRetrieveResult:
        started_at = perf_counter()
        knowledge_base = await self._knowledge_bases.get_by_id_and_tenant(
            request.kb_id, request.tenant_id
        )
        if knowledge_base is None:
            raise KnowledgeBaseNotFoundError()

        # 请求级 top_k 覆盖系统配置。
        top_k = request.top_k if request.top_k is not None else self._top_k

        query_vectors = await self._embedding_provider.embed([request.query])
        if len(query_vectors) != 1:
            raise ValueError("embedding provider did not return a query vector")
        raw_chunks = await self._vector_store.similarity_search(
            query_vectors[0],
            tenant_id=request.tenant_id,
            kb_id=request.kb_id,
            top_k=top_k,
        )

        final_chunks, rerank_meta = await self._apply_rerank(request, raw_chunks)

        chunks = [RetrievedChunk.model_validate(chunk) for chunk in final_chunks]
        latency_ms = round((perf_counter() - started_at) * 1000)

        # 业务关键节点：检索完成
        log_event(
            logger,
            logging.INFO,
            "RAG_RETRIEVE",
            kb_id=request.kb_id,
            query=request.query,
            hits=len(chunks),
            top_k=top_k,
            rerank_enabled=rerank_meta.enabled,
            rerank_degraded=rerank_meta.degraded or False,
            latency=f"{latency_ms}ms",
        )

        retrieval_log = RetrievalLog(
            id=generate_retrieval_log_id(),
            tenant_id=request.tenant_id,
            kb_id=request.kb_id,
            user_id=request.user_id,
            query=request.query,
            # 记录原始 dict（含 rerank_score 与 rerank_reason），reason 仅供排查。
            retrieved_chunks=[dict(chunk) for chunk in final_chunks],
            top_k=top_k,
            vector_store=self._vector_store.name,
            latency_ms=latency_ms,
        )
        await self._retrieval_logs.add(retrieval_log)
        await self._session.commit()

        return RagRetrieveResult(
            query=request.query,
            kb_id=request.kb_id,
            retrieved_chunks=chunks,
            metadata=RetrievalMetadata(
                top_k=top_k,
                vector_store=self._vector_store.name,
                rerank=rerank_meta,
            ),
        )

    # -- 重排 -------------------------------------------------------------- #
    async def _apply_rerank(
        self,
        request: RagRetrieveRequest,
        raw_chunks: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], RerankMetadata]:
        """在向量召回后执行重排，失败时自动降级为原始向量排序。

        embedding / 向量检索失败会让接口失败；重排失败不会，返回原始向量排序。
        """
        options = request.rerank_options
        rerank_enabled = (
            options.enabled
            if options is not None and options.enabled is not None
            else self._rerank_enabled
        )

        if not rerank_enabled:
            return raw_chunks, RerankMetadata(enabled=False)

        top_n = (
            options.top_n
            if options is not None and options.top_n is not None
            else self._rerank_top_n
        )
        candidate_count = self._candidate_count(raw_chunks)
        base_meta = self._provider_metadata(top_n=top_n, candidate_count=candidate_count)

        try:
            reranked = await self._rerank_provider.rerank(
                query=request.query,
                chunks=raw_chunks,
                top_n=top_n,
            )
            # 成功时显式 degraded=False，使启用重排时 degraded 恒为布尔值。
            return reranked, RerankMetadata(enabled=True, degraded=False, **base_meta)
        except Exception as exc:  # 重排失败不应让整个检索接口失败
            error_reason = _error_reason(exc)
            log_event(
                logger,
                logging.WARNING,
                "RAG_RERANK_DEGRADED",
                kb_id=request.kb_id,
                provider=base_meta.get("provider"),
                error=error_reason,
            )
            return (
                raw_chunks[:top_n],
                RerankMetadata(enabled=True, degraded=True, error=error_reason, **base_meta),
            )

    def _candidate_count(self, raw_chunks: list[dict[str, Any]]) -> int:
        """进入重排的候选数量（受 provider 的 max_candidates 限制）。"""
        max_candidates = getattr(self._rerank_provider, "max_candidates", None)
        if max_candidates is None:
            return len(raw_chunks)
        return min(len(raw_chunks), max_candidates)

    def _provider_metadata(self, *, top_n: int, candidate_count: int) -> dict[str, Any]:
        """从 provider 提取写入 metadata.rerank 的描述信息。"""
        return {
            "provider": getattr(self._rerank_provider, "name", "unknown"),
            "llm_provider": getattr(self._rerank_provider, "llm_provider_name", None),
            "model": getattr(self._rerank_provider, "model", None),
            "top_n": top_n,
            "candidate_count": candidate_count,
        }
