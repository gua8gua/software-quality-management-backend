from typing import Any

from app.providers.llm.base import LLMProvider
from app.providers.rerank.llm import LLMRerankProvider
from app.providers.rerank.noop import NoopRerankProvider


class FakeLLMProvider(LLMProvider):
    """记录调用参数并返回预设 JSON，绝不发真实 HTTP 请求。"""

    name = "fake_llm"
    model = "fake-model"

    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def chat_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict,
        temperature: float = 0.0,
        timeout_seconds: int | None = None,
    ) -> dict:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_payload": user_payload,
                "temperature": temperature,
                "timeout_seconds": timeout_seconds,
            }
        )
        return self._result


def _chunks() -> list[dict[str, Any]]:
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


async def test_llm_rerank_sorts_by_score_and_returns_top_n() -> None:
    llm = FakeLLMProvider(
        {
            "rankings": [
                {"chunk_id": "chunk_001", "rerank_score": 0.95, "reason": "直接回答时限"},
                {"chunk_id": "chunk_002", "rerank_score": 0.32},
                {"chunk_id": "chunk_003", "rerank_score": 0.10},
            ]
        }
    )
    provider = LLMRerankProvider(llm_provider=llm)

    result = await provider.rerank(query="退款需要几天内申请？", chunks=_chunks(), top_n=2)

    assert [chunk["chunk_id"] for chunk in result] == ["chunk_001", "chunk_002"]
    assert result[0]["rerank_score"] == 0.95
    # 保留原始字段
    assert result[0]["score"] == 0.86
    assert result[0]["document_id"] == "doc_001"
    assert result[0]["metadata"] == {"chunk_index": 0}


async def test_llm_rerank_keeps_reason_for_logging() -> None:
    llm = FakeLLMProvider(
        {"rankings": [{"chunk_id": "chunk_001", "rerank_score": 0.9, "reason": "命中时限"}]}
    )
    provider = LLMRerankProvider(llm_provider=llm)

    result = await provider.rerank(query="q", chunks=_chunks(), top_n=3)

    by_id = {chunk["chunk_id"]: chunk for chunk in result}
    assert by_id["chunk_001"]["rerank_reason"] == "命中时限"
    assert "rerank_reason" not in by_id["chunk_002"]


async def test_llm_rerank_defaults_missing_chunks_to_zero_and_ignores_unknown() -> None:
    llm = FakeLLMProvider(
        {
            "rankings": [
                {"chunk_id": "chunk_003", "rerank_score": 0.8},
                {"chunk_id": "does_not_exist", "rerank_score": 1.0},
            ]
        }
    )
    provider = LLMRerankProvider(llm_provider=llm)

    result = await provider.rerank(query="q", chunks=_chunks(), top_n=3)

    by_id = {chunk["chunk_id"]: chunk for chunk in result}
    # 未知 chunk_id 被忽略
    assert "does_not_exist" not in by_id
    # 漏掉的 chunk_001 / chunk_002 默认 0，排到 chunk_003 之后
    assert result[0]["chunk_id"] == "chunk_003"
    assert by_id["chunk_001"]["rerank_score"] == 0.0
    assert by_id["chunk_002"]["rerank_score"] == 0.0


async def test_llm_rerank_clamps_score_to_unit_interval() -> None:
    llm = FakeLLMProvider(
        {
            "rankings": [
                {"chunk_id": "chunk_001", "rerank_score": 1.7},
                {"chunk_id": "chunk_002", "rerank_score": -0.5},
            ]
        }
    )
    provider = LLMRerankProvider(llm_provider=llm)

    result = await provider.rerank(query="q", chunks=_chunks(), top_n=3)

    by_id = {chunk["chunk_id"]: chunk for chunk in result}
    assert by_id["chunk_001"]["rerank_score"] == 1.0
    assert by_id["chunk_002"]["rerank_score"] == 0.0


async def test_llm_rerank_limits_candidates_and_truncates_content() -> None:
    llm = FakeLLMProvider({"rankings": []})
    long_content = "x" * 5000
    chunks = [
        {
            "document_id": f"doc_{i}",
            "chunk_id": f"chunk_{i}",
            "title": "t",
            "content": long_content,
            "score": 0.5,
        }
        for i in range(30)
    ]
    provider = LLMRerankProvider(llm_provider=llm, max_candidates=20, chunk_max_chars=1000)

    await provider.rerank(query="q", chunks=chunks, top_n=5)

    payload = llm.calls[0]["user_payload"]
    # 只取前 max_candidates 个进入大模型
    assert len(payload["candidates"]) == 20
    assert payload["top_n"] == 5
    # 内容被截断
    assert all(len(c["content"]) == 1000 for c in payload["candidates"])


async def test_llm_rerank_sends_structured_candidate_fields() -> None:
    llm = FakeLLMProvider({"rankings": []})
    provider = LLMRerankProvider(llm_provider=llm)

    await provider.rerank(query="退款需要几天内申请？", chunks=_chunks(), top_n=5)

    payload = llm.calls[0]["user_payload"]
    assert payload["query"] == "退款需要几天内申请？"
    candidate = payload["candidates"][0]
    assert candidate == {
        "chunk_id": "chunk_001",
        "document_id": "doc_001",
        "title": "退款政策",
        "content": "用户可在订单完成后 7 天内申请退款。",
        "vector_score": 0.86,
    }


async def test_llm_rerank_empty_chunks_returns_empty() -> None:
    llm = FakeLLMProvider({"rankings": []})
    provider = LLMRerankProvider(llm_provider=llm)

    result = await provider.rerank(query="q", chunks=[], top_n=5)

    assert result == []
    assert llm.calls == []  # 无候选时不调用大模型


async def test_llm_rerank_exposes_provider_metadata() -> None:
    llm = FakeLLMProvider({"rankings": []})
    provider = LLMRerankProvider(llm_provider=llm, max_candidates=20)

    assert provider.name == "llm"
    assert provider.llm_provider_name == "fake_llm"
    assert provider.model == "fake-model"
    assert provider.max_candidates == 20


async def test_noop_rerank_preserves_order_and_sets_null_score() -> None:
    provider = NoopRerankProvider()

    result = await provider.rerank(query="q", chunks=_chunks(), top_n=2)

    assert [chunk["chunk_id"] for chunk in result] == ["chunk_001", "chunk_002"]
    assert all(chunk["rerank_score"] is None for chunk in result)
    # 保留原始字段
    assert result[0]["score"] == 0.86
    assert provider.name == "noop"
