"""重排（rerank）相关的数据结构。

包含三类结构：

* 请求侧：:class:`RerankOptions` —— ``/rag/retrieve`` 可选的重排参数。
* 大模型交互：:class:`RerankCandidate` / :class:`RerankRanking` —— 传给大模型以及
  约束大模型返回的结构化 JSON。
* 响应侧：:class:`RerankMetadata` —— 写入 ``metadata.rerank`` 的重排元信息。
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RerankOptions(BaseModel):
    """请求体中的可选重排参数。

    字段为 ``None`` 时回退到系统配置（``.env``）。
    """

    model_config = ConfigDict(extra="ignore")

    enabled: bool | None = None
    top_n: int | None = Field(default=None, gt=0, le=100)


class RerankCandidate(BaseModel):
    """送入大模型的单个候选 chunk（已截断、已脱敏）。"""

    chunk_id: str
    document_id: str
    title: str
    content: str
    vector_score: float


class RerankRanking(BaseModel):
    """大模型返回的单个 chunk 打分结果。"""

    model_config = ConfigDict(extra="ignore")

    chunk_id: str
    rerank_score: float
    reason: str | None = None


class RerankResult(BaseModel):
    """大模型必须返回的严格 JSON 结构。"""

    model_config = ConfigDict(extra="ignore")

    rankings: list[RerankRanking] = Field(default_factory=list)


class RerankMetadata(BaseModel):
    """写入接口响应 ``metadata.rerank`` 的重排元信息。

    未启用的字段保持 ``None``，由路由层 ``exclude_none`` 统一裁剪。
    """

    enabled: bool
    degraded: bool | None = None
    provider: str | None = None
    llm_provider: str | None = None
    model: str | None = None
    top_n: int | None = None
    candidate_count: int | None = None
    error: str | None = None


def build_rerank_payload(
    *,
    query: str,
    candidates: list[dict[str, Any]],
    top_n: int,
) -> dict[str, Any]:
    """组装传给大模型的结构化 user payload。"""

    return {"query": query, "candidates": candidates, "top_n": top_n}
