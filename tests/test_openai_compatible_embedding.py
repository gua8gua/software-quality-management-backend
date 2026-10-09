import json

import httpx
import pytest

from app.providers.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider


@pytest.mark.asyncio
async def test_remote_embedding_uses_instruction_batches_and_exact_token_preflight():
    calls: list[tuple[str, dict, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append((request.url.path, payload, request.headers.get("authorization")))
        if request.url.path == "/tokenize":
            count = 7 if "first" in payload["prompt"] else 11
            return httpx.Response(200, json={"count": count, "max_model_len": 4096, "tokens": []})
        assert request.url.path == "/v1/embeddings"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": [float(index), 1.0]}
                    for index, _ in enumerate(payload["input"])
                ]
            },
        )

    provider = OpenAICompatibleEmbeddingProvider(
        base_url="http://models.example/v1",
        api_key="secret",
        model="qwen3-embedding-0.6b-1024",
        batch_size=2,
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )

    report = await provider.query_input_length_report(
        ["first", "second"], "retrieve design evidence"
    )
    vectors = await provider.encode_queries(["first", "second"], "retrieve design evidence")

    assert report == {
        "status": "ok",
        "model": "qwen3-embedding-0.6b-1024",
        "count": 2,
        "max_tokens": 4096,
        "observed_max_tokens": 11,
        "oversize_indexes": [],
        "token_lengths": [7, 11],
    }
    assert vectors == [[0.0, 1.0], [1.0, 1.0]]
    embedding_payload = next(payload for path, payload, _ in calls if path == "/v1/embeddings")
    assert embedding_payload["input"] == [
        "Instruct: retrieve design evidence\nQuery:first",
        "Instruct: retrieve design evidence\nQuery:second",
    ]
    tokenize_payloads = [payload for path, payload, _ in calls if path == "/tokenize"]
    assert [payload["prompt"] for payload in tokenize_payloads] == embedding_payload["input"]
    assert all(auth == "Bearer secret" for _, _, auth in calls)


@pytest.mark.asyncio
async def test_remote_embedding_reports_oversize_without_calling_inference():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/tokenize"
        return httpx.Response(200, json={"count": 4100, "max_model_len": 4096, "tokens": []})

    provider = OpenAICompatibleEmbeddingProvider(
        base_url="http://models.example/v1",
        api_key="",
        model="embedding",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )

    report = await provider.input_length_report(["oversized"])
    assert report["status"] == "oversize"
    assert report["oversize_indexes"] == [0]
