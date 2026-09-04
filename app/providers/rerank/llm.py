"""基于通用 :class:`LLMProvider` 的大模型打分重排实现。

本 provider 不直接依赖 DeepSeek、百炼或任何 HTTP client，只通过
:meth:`LLMProvider.chat_json` 调用大模型，因此切换底层模型只需替换 LLMProvider 配置。
"""

from typing import Any

from app.providers.llm.base import LLMProvider
from app.providers.rerank.base import RerankProvider
from app.schemas.rerank import RerankResult, build_rerank_payload

RERANK_SYSTEM_PROMPT = """\
你是 RAG 检索重排器。你的任务是根据用户问题，对候选知识片段进行相关性打分和排序。

要求：
1. 只判断候选片段是否有助于回答用户问题。
2. 不要回答用户问题。
3. 不要编造候选片段中不存在的信息。
4. 每个候选片段给出 0 到 1 之间的 rerank_score。
5. 分数越高表示越相关、越适合作为 RAG 上下文。
6. 只返回 JSON，不要返回 Markdown，不要返回解释性文字。

返回格式（严格 JSON）：
{
  "rankings": [
    {"chunk_id": "chunk_001", "rerank_score": 0.95, "reason": "打分原因"}
  ]
}"""


class LLMRerankProvider(RerankProvider):
    name = "llm"

    def __init__(
        self,
        *,
        llm_provider: LLMProvider,
        max_candidates: int = 20,
        chunk_max_chars: int = 1000,
        temperature: float = 0.0,
        timeout_seconds: int | None = None,
    ) -> None:
        self._llm = llm_provider
        self.max_candidates = max_candidates
        self._chunk_max_chars = chunk_max_chars
        self._temperature = temperature
        self._timeout_seconds = timeout_seconds

    # -- 元信息（供 RagService 写入 metadata.rerank） ----------------------- #
    @property
    def llm_provider_name(self) -> str:
        return getattr(self._llm, "name", "unknown")

    @property
    def model(self) -> str:
        return getattr(self._llm, "model", "")

    async def rerank(
        self,
        *,
        query: str,
        chunks: list[dict],
        top_n: int,
    ) -> list[dict]:
        if not chunks:
            return []

        candidate_chunks = chunks[: self.max_candidates]
        candidates = [self._to_candidate(chunk) for chunk in candidate_chunks]
        payload = build_rerank_payload(query=query, candidates=candidates, top_n=top_n)

        result = await self._llm.chat_json(
            system_prompt=RERANK_SYSTEM_PROMPT,
            user_payload=payload,
            temperature=self._temperature,
            timeout_seconds=self._timeout_seconds,
        )

        score_map, reason_map = self._parse_rankings(result, candidates)
        return self._merge_and_sort(candidate_chunks, score_map, reason_map, top_n)

    # -- 内部辅助 ---------------------------------------------------------- #
    def _to_candidate(self, chunk: dict[str, Any]) -> dict[str, Any]:
        """组装送入大模型的候选结构：截断内容、只保留必要字段。"""
        content = str(chunk.get("content", "") or "")
        if len(content) > self._chunk_max_chars:
            content = content[: self._chunk_max_chars]
        return {
            "chunk_id": chunk.get("chunk_id", ""),
            "document_id": chunk.get("document_id", ""),
            "title": chunk.get("title", ""),
            "content": content,
            "vector_score": float(chunk.get("score", 0.0) or 0.0),
        }

    @staticmethod
    def _parse_rankings(
        result: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> tuple[dict[str, float], dict[str, str]]:
        """解析大模型返回，构建 chunk_id -> score / reason 映射。

        * 用 :class:`RerankResult` 做结构校验，容忍字段缺失。
        * 忽略不存在的 chunk_id。
        * rerank_score 截断到 [0, 1]。
        """
        valid_ids = {candidate["chunk_id"] for candidate in candidates}
        score_map: dict[str, float] = {}
        reason_map: dict[str, str] = {}

        try:
            parsed = RerankResult.model_validate(result)
        except Exception:  # 结构异常时退化为空排序（保持向量顺序）
            return score_map, reason_map

        for ranking in parsed.rankings:
            if ranking.chunk_id not in valid_ids:
                continue
            score = max(0.0, min(1.0, float(ranking.rerank_score)))
            score_map[ranking.chunk_id] = score
            if ranking.reason:
                reason_map[ranking.chunk_id] = ranking.reason
        return score_map, reason_map

    @staticmethod
    def _merge_and_sort(
        candidate_chunks: list[dict[str, Any]],
        score_map: dict[str, float],
        reason_map: dict[str, str],
        top_n: int,
    ) -> list[dict[str, Any]]:
        """合并打分到原始 chunk，按 rerank_score 降序稳定排序，返回 top_n。

        * 保留每个 chunk 的原始字段。
        * 大模型漏掉的 chunk 默认 rerank_score = 0（排到末尾）。
        * ``rerank_reason`` 仅用于日志，业务接口层会裁剪掉。
        """
        scored: list[tuple[int, dict[str, Any]]] = []
        for index, chunk in enumerate(candidate_chunks):
            merged = dict(chunk)
            chunk_id = chunk.get("chunk_id", "")
            merged["rerank_score"] = score_map.get(chunk_id, 0.0)
            if chunk_id in reason_map:
                merged["rerank_reason"] = reason_map[chunk_id]
            scored.append((index, merged))

        scored.sort(key=lambda item: (-item[1]["rerank_score"], item[0]))
        return [merged for _, merged in scored[:top_n]]
