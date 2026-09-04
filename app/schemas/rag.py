from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.rerank import RerankMetadata, RerankOptions


class RagRetrieveRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    kb_id: str = Field(min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=10_000)
    # 向量召回候选数量；为空时回退到系统配置 TOP_K。启用 rerank 时建议大于 top_n。
    top_k: int | None = Field(default=None, gt=0, le=100)
    # 可选重排参数；为空时按系统配置决定是否启用。
    rerank_options: RerankOptions | None = None


class RetrievedChunk(BaseModel):
    # 重排返回的 rerank_reason 等额外字段仅用于日志，这里通过 extra="ignore" 裁剪，
    # 不暴露给业务接口。
    model_config = ConfigDict(extra="ignore")

    document_id: str
    chunk_id: str
    title: str
    content: str
    score: float
    # 大模型重排分数；未启用 rerank 时为 None。
    rerank_score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalMetadata(BaseModel):
    top_k: int
    vector_store: str
    rerank: RerankMetadata


class RagRetrieveResult(BaseModel):
    query: str
    kb_id: str
    retrieved_chunks: list[RetrievedChunk]
    metadata: RetrievalMetadata
