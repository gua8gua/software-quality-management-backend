from app.providers.documents.base import DocumentParser
from app.providers.embedding.base import EmbeddingProvider
from app.providers.vectorstores.base import VectorStore
from app.utils.id_generator import generate_chunk_id
from app.utils.text_splitter import TextSplitter


class IndexingService:
    def __init__(
        self,
        *,
        parser: DocumentParser,
        splitter: TextSplitter,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
    ) -> None:
        self._parser = parser
        self._splitter = splitter
        self._embedding_provider = embedding_provider
        self._vector_store = vector_store

    async def index_document(
        self,
        *,
        document_id: str,
        tenant_id: str,
        kb_id: str,
        title: str,
        content: str,
        source_type: str = "text",
    ) -> int:
        text = await self._parser.parse(content, source_type=source_type)
        parts = self._splitter.split(text)
        if not parts:
            raise ValueError("document produced no chunks")
        embeddings = await self._embedding_provider.embed(parts)
        if len(embeddings) != len(parts):
            raise ValueError("embedding count does not match chunk count")

        chunks = [
            {
                "id": generate_chunk_id(),
                "tenant_id": tenant_id,
                "kb_id": kb_id,
                "document_id": document_id,
                "title": title,
                "content": part,
                "chunk_metadata": {"chunk_index": index, "source_type": source_type},
                "embedding": embedding,
            }
            for index, (part, embedding) in enumerate(zip(parts, embeddings, strict=True))
        ]
        await self._vector_store.add_chunks(chunks)
        return len(chunks)

