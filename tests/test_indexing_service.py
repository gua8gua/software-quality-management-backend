from typing import Any

import pytest

from app.providers.documents.base import DocumentParser
from app.providers.embedding.base import EmbeddingProvider
from app.providers.vectorstores.base import VectorStore
from app.services.indexing_service import IndexingService
from app.utils.text_splitter import TextSplitter


class FakeParser(DocumentParser):
    async def parse(self, content: str, *, source_type: str = "text") -> str:
        return content.upper()


class FakeSplitter(TextSplitter):
    def split(self, text: str) -> list[str]:
        return [text[:3], text[3:]]


class FakeEmbeddingProvider(EmbeddingProvider):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(index)] * 3 for index, _ in enumerate(texts)]


class FakeVectorStore(VectorStore):
    name = "fake"

    def __init__(self) -> None:
        self.chunks: list[dict[str, Any]] = []

    async def add_chunks(self, chunks: list[dict[str, Any]]) -> None:
        self.chunks = chunks

    async def similarity_search(
        self,
        query_vector: list[float],
        *,
        tenant_id: str,
        kb_id: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        return []

    async def delete_by_document_id(self, document_id: str) -> None:
        return None


@pytest.mark.asyncio
async def test_indexing_service_orchestrates_parser_splitter_embedding_and_store() -> None:
    store = FakeVectorStore()
    service = IndexingService(
        parser=FakeParser(),
        splitter=FakeSplitter(),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=store,
    )

    count = await service.index_document(
        document_id="document-id",
        tenant_id="tenant",
        kb_id="kb-id",
        title="title",
        content="abcdef",
    )

    assert count == 2
    assert [item["content"] for item in store.chunks] == ["ABC", "DEF"]
    assert [item["chunk_metadata"]["chunk_index"] for item in store.chunks] == [0, 1]
    assert all(item["document_id"] == "document-id" for item in store.chunks)

