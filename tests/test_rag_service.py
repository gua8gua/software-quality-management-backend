from typing import Any

import pytest

from app.core.exceptions import LLMProviderError
from app.providers.embedding.base import EmbeddingProvider
from app.providers.rerank.base import RerankProvider
from app.providers.vectorstores.base import VectorStore
from app.schemas.rag import RagRetrieveRequest
from app.schemas.rerank import RerankOptions
from app.services.rag_service import RagService


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class FakeKnowledgeBases:
    async def get_by_id_and_tenant(self, kb_id: str, tenant_id: str) -> object:
        return object()


class FakeLogs:
    def __init__(self) -> None:
        self.log = None

    async def add(self, log: object) -> object:
        self.log = log
        return log


class FakeEmbedding(EmbeddingProvider):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3]]


def _raw_chunks() -> list[dict[str, Any]]:
    return [
        {
            "document_id": "doc_001",
            "chunk_id": "chunk_001",
            "title": "退款政策",
            "content": "用户可在订单完成后 7 天内申请退款。",
            "score": 0.86,
            "metadata": {"chunk_index": 0},
        },
        {
            "document_id": "doc_001",
            "chunk_id": "chunk_002",
            "title": "退款政策",
            "content": "特殊商品不支持无理由退款。",
            "score": 0.73,
            "metadata": {"chunk_index": 1},
        },
        {
            "document_id": "doc_002",
            "chunk_id": "chunk_003",
            "title": "配送说明",
            "content": "普通商品 3 日内发货。",
            "score": 0.60,
            "metadata": {"chunk_index": 0},
        },
    ]


class FakeStore(VectorStore):
    name = "fakevector"

    def __init__(self, chunks: list[dict[str, Any]] | None = None) -> None:
        self._chunks = chunks if chunks is not None else _raw_chunks()
        self.last_top_k: int | None = None

    async def add_chunks(self, chunks: list[dict[str, Any]]) -> None:
        return None

    async def similarity_search(
        self,
        query_vector: list[float],
        *,
        tenant_id: str,
        kb_id: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        self.last_top_k = top_k
        return self._chunks[:top_k]

    async def delete_by_document_id(self, document_id: str) -> None:
        return None


class FakeRerankProvider(RerankProvider):
    name = "llm"
    max_candidates = 20
    llm_provider_name = "fake_llm"
    model = "fake-model"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def rerank(self, *, query: str, chunks: list[dict], top_n: int) -> list[dict]:
        self.calls.append({"query": query, "chunks": chunks, "top_n": top_n})
        result = []
        # 反转顺序并赋分，证明接口返回的是重排结果而非向量顺序。
        ordered = list(reversed(chunks))[:top_n]
        for index, chunk in enumerate(ordered):
            merged = dict(chunk)
            merged["rerank_score"] = round(0.9 - index * 0.1, 2)
            merged["rerank_reason"] = "调试原因"
            result.append(merged)
        return result


class FailingRerankProvider(RerankProvider):
    name = "llm"
    max_candidates = 20
    llm_provider_name = "fake_llm"
    model = "fake-model"

    async def rerank(self, *, query: str, chunks: list[dict], top_n: int) -> list[dict]:
        raise LLMProviderError("rerank response json parse failed")


def _build_service(
    *,
    store: FakeStore | None = None,
    rerank_provider: RerankProvider | None = None,
    rerank_enabled: bool = False,
    rerank_top_n: int = 5,
    top_k: int = 5,
) -> tuple[RagService, FakeLogs, FakeSession, FakeStore]:
    logs = FakeLogs()
    session = FakeSession()
    fake_store = store or FakeStore()
    service = RagService(
        knowledge_base_repository=FakeKnowledgeBases(),
        retrieval_log_repository=logs,
        embedding_provider=FakeEmbedding(),
        vector_store=fake_store,
        session=session,
        top_k=top_k,
        rerank_provider=rerank_provider,
        rerank_enabled=rerank_enabled,
        rerank_top_n=rerank_top_n,
    )
    return service, logs, session, fake_store


@pytest.mark.asyncio
async def test_rag_service_returns_structured_chunks_and_writes_log() -> None:
    service, logs, session, store = _build_service(store=FakeStore(_raw_chunks()[:1]))
    request = RagRetrieveRequest(
        tenant_id="tenant",
        kb_id="00000000-0000-0000-0000-000000000001",
        user_id="user",
        query="退款期限？",
    )

    result = await service.retrieve(request)

    assert result.retrieved_chunks[0].content == "用户可在订单完成后 7 天内申请退款。"
    assert result.retrieved_chunks[0].score == 0.86
    assert result.metadata.vector_store == "fakevector"
    assert result.metadata.rerank.enabled is False
    assert store.last_top_k == 5
    assert logs.log.user_id == "user"
    assert logs.log.retrieved_chunks[0]["chunk_id"] == "chunk_001"
    assert session.commits == 1


@pytest.mark.asyncio
async def test_rerank_disabled_by_default_keeps_vector_order() -> None:
    service, _logs, _session, _store = _build_service(rerank_enabled=False)
    request = RagRetrieveRequest(
        tenant_id="tenant",
        kb_id="00000000-0000-0000-0000-000000000001",
        user_id="user",
        query="退款期限？",
    )

    result = await service.retrieve(request)

    assert result.metadata.rerank.enabled is False
    # 未重排：返回全部向量召回结果，rerank_score 为 None
    assert [c.chunk_id for c in result.retrieved_chunks] == [
        "chunk_001",
        "chunk_002",
        "chunk_003",
    ]
    assert all(c.rerank_score is None for c in result.retrieved_chunks)


@pytest.mark.asyncio
async def test_rerank_enabled_via_request_returns_reranked_top_n_and_metadata() -> None:
    rerank_provider = FakeRerankProvider()
    service, logs, _session, _store = _build_service(
        rerank_provider=rerank_provider, rerank_enabled=False, rerank_top_n=5
    )
    request = RagRetrieveRequest(
        tenant_id="tenant",
        kb_id="00000000-0000-0000-0000-000000000001",
        user_id="user",
        query="退款需要几天内申请？",
        top_k=20,
        rerank_options=RerankOptions(enabled=True, top_n=2),
    )

    result = await service.retrieve(request)

    # 重排生效：返回 top_n=2，且为重排后的顺序（FakeRerankProvider 反转）
    assert [c.chunk_id for c in result.retrieved_chunks] == ["chunk_003", "chunk_002"]
    assert result.retrieved_chunks[0].rerank_score == 0.9
    # rerank_reason 不应暴露给业务接口
    assert not hasattr(result.retrieved_chunks[0], "rerank_reason")

    # metadata.rerank 描述信息
    meta = result.metadata.rerank
    assert meta.enabled is True
    assert meta.degraded is False
    assert meta.provider == "llm"
    assert meta.llm_provider == "fake_llm"
    assert meta.model == "fake-model"
    assert meta.top_n == 2
    assert meta.candidate_count == 3

    # provider 收到 top_n 与候选
    assert rerank_provider.calls[0]["top_n"] == 2
    assert len(rerank_provider.calls[0]["chunks"]) == 3

    # 日志记录重排后的 chunk（含 rerank_score 与 rerank_reason）
    assert logs.log.retrieved_chunks[0]["rerank_score"] == 0.9
    assert logs.log.retrieved_chunks[0]["rerank_reason"] == "调试原因"
    # top_k 覆盖系统配置
    assert logs.log.top_k == 20


@pytest.mark.asyncio
async def test_rerank_failure_degrades_to_vector_order_without_failing_request() -> None:
    service, _logs, _session, _store = _build_service(
        rerank_provider=FailingRerankProvider(), rerank_enabled=True, rerank_top_n=2
    )
    request = RagRetrieveRequest(
        tenant_id="tenant",
        kb_id="00000000-0000-0000-0000-000000000001",
        user_id="user",
        query="退款需要几天内申请？",
    )

    # 重排失败不应让接口失败
    result = await service.retrieve(request)

    meta = result.metadata.rerank
    assert meta.enabled is True
    assert meta.degraded is True
    assert "json parse failed" in (meta.error or "")
    # 降级为原始向量排序的前 top_n 个
    assert [c.chunk_id for c in result.retrieved_chunks] == ["chunk_001", "chunk_002"]
    assert all(c.rerank_score is None for c in result.retrieved_chunks)


@pytest.mark.asyncio
async def test_request_top_k_overrides_settings() -> None:
    service, _logs, _session, store = _build_service(top_k=5)
    request = RagRetrieveRequest(
        tenant_id="tenant",
        kb_id="00000000-0000-0000-0000-000000000001",
        user_id="user",
        query="退款期限？",
        top_k=2,
    )

    await service.retrieve(request)

    assert store.last_top_k == 2
