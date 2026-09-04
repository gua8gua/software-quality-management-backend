from app.providers.documents.base import DocumentParser


class PlainTextDocumentParser(DocumentParser):
    async def parse(self, content: str, *, source_type: str = "text") -> str:
        if source_type not in {"text", "txt", "md"}:
            raise ValueError(f"unsupported source type: {source_type}")
        normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            raise ValueError("document content is empty")
        return normalized

