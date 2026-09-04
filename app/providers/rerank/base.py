"""统一重排（rerank）抽象。

重排发生在向量召回之后：对候选 chunk 重新打分并排序，返回 ``top_n`` 个结果。
不同实现（大模型打分、cross-encoder、占位不重排等）都遵循同一接口。
"""

from abc import ABC, abstractmethod


class RerankProvider(ABC):
    """统一重排接口。"""

    #: provider 名称，例如 ``llm`` / ``noop``，用于日志与接口元信息。
    name: str = "base"

    #: 单次最多送入重排的候选数量；``None`` 表示不限制。用于接口元信息。
    max_candidates: int | None = None

    @abstractmethod
    async def rerank(
        self,
        *,
        query: str,
        chunks: list[dict],
        top_n: int,
    ) -> list[dict]:
        """对向量召回的候选 chunk 重排并返回 ``top_n`` 个结果。

        参数
        ----
        query:
            用户原始 query。
        chunks:
            向量召回后的候选 chunk 列表（每个为 dict，含 ``chunk_id``、``content`` 等）。
        top_n:
            最终返回的 chunk 数量。

        返回
        ----
        重排后的 chunk 列表。每个 chunk 保留原始字段，并新增 ``rerank_score``
        （未真正打分时可为 ``None``）。
        """
