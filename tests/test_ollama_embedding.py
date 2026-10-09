import json
import math

import httpx
import pytest

from app.providers.embeddings.ollama import (
    LocalEmbeddingUnavailableError,
    OllamaEmbeddingProvider,
)


@pytest.mark.asyncio
async def test_ollama_embedding_uses_native_api_batches_and_disables_truncation():
    calls: list[tuple[str, dict, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append((request.url.path, payload, request.headers.get("authorization")))
        return httpx.Response(
            200,
            json={"embeddings": [[float(index), 1.0] for index, _ in enumerate(payload["input"])]},
        )

    provider = OllamaEmbeddingProvider(
        base_url="http://ollama.example/v1",
        api_key="secret",
        model="qwen3-embedding:4b",
        batch_size=2,
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )

    report = await provider.query_input_length_report(
        ["first", "second"], "retrieve design evidence"
    )
    vectors = await provider.encode_queries(
        ["first", "second", "third"], "retrieve design evidence"
    )

    assert report["status"] == "provider_enforced_no_truncation"
    assert report["count"] == 2
    assert report["oversize_indexes"] == []
    assert [path for path, _, _ in calls] == ["/api/embed", "/api/embed"]
    assert calls[0][1]["input"] == [
        "Instruct: retrieve design evidence\nQuery:first",
        "Instruct: retrieve design evidence\nQuery:second",
    ]
    assert calls[1][1]["input"] == [
        "Instruct: retrieve design evidence\nQuery:third"
    ]
    assert all(payload["truncate"] is False for _, payload, _ in calls)
    assert all(auth == "Bearer secret" for _, _, auth in calls)
    assert vectors == [[0.0, 1.0], [1.0, 1.0], [0.0, 1.0]]


@pytest.mark.asyncio
async def test_ollama_embedding_documents_have_no_query_instruction():
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["input"] == ["raw canonical projection"]
        return httpx.Response(200, json={"embeddings": [[1, 2, 3]]})

    provider = OllamaEmbeddingProvider(
        base_url="http://ollama.example/api",
        model="qwen3-embedding:4b",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )

    assert await provider.encode_documents(["raw canonical projection"]) == [[1.0, 2.0, 3.0]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_body", "message"),
    [
        ({"embeddings": []}, "different number"),
        ({"embeddings": [[1.0], [1.0, 2.0]]}, "inconsistent vector dimensions"),
        ({"embeddings": [[math.inf]]}, "non-finite vector"),
    ],
)
async def test_ollama_embedding_rejects_invalid_responses(response_body, message):
    async def handler(_: httpx.Request) -> httpx.Response:
        if response_body == {"embeddings": [[math.inf]]}:
            return httpx.Response(
                200,
                content=b'{"embeddings":[[Infinity]]}',
                headers={"Content-Type": "application/json"},
            )
        return httpx.Response(200, json=response_body)

    provider = OllamaEmbeddingProvider(
        base_url="http://ollama.example",
        model="qwen3-embedding:4b",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )

    texts = ["one", "two"] if len(response_body.get("embeddings", [])) == 2 else ["one"]
    with pytest.raises(ValueError, match=message):
        await provider.encode_documents(texts)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
async def test_ollama_embedding_reports_local_service_unavailable(failure):
    async def handler(request: httpx.Request) -> httpx.Response:
        failure.request = request
        raise failure

    provider = OllamaEmbeddingProvider(
        base_url="http://ollama.example",
        model="qwen3-embedding:4b",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )

    with pytest.raises(LocalEmbeddingUnavailableError, match="LOCAL_EMBEDDING_UNAVAILABLE"):
        await provider.encode_documents(["one"])
