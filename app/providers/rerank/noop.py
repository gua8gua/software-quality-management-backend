"""占位重排实现：不做任何重排，保持原始向量召回顺序。

用于通过配置（``RERANK_PROVIDER=noop``）关闭大模型重排，同时保持接口结构一致——
返回的 chunk 仍带 ``rerank_score`` 字段，但值为 ``None``。
"""

from app.providers.rerank.base import RerankProvider


class NoopRerankProvider(RerankProvider):
    """不重排的占位 Provider。"""

    name = "noop"
    max_candidates = None

    async def rerank(
        self,
        *,
        query: str,
        chunks: list[dict],
        top_n: int,
    ) -> list[dict]:
        result: list[dict] = []
        for chunk in chunks[:top_n]:
            merged = dict(chunk)
            merged["rerank_score"] = None
            result.append(merged)
        return result
