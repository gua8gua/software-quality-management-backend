from abc import ABC, abstractmethod


class TextSplitter(ABC):
    @abstractmethod
    def split(self, text: str) -> list[str]:
        """Split text into non-empty overlapping chunks."""


class CharacterTextSplitter(TextSplitter):
    def __init__(self, *, chunk_size: int, chunk_overlap: int) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def split(self, text: str) -> list[str]:
        normalized = text.strip()
        if not normalized:
            return []

        chunks: list[str] = []
        start = 0
        while start < len(normalized):
            hard_end = min(start + self._chunk_size, len(normalized))
            end = hard_end
            if hard_end < len(normalized):
                lower_bound = start + max(1, self._chunk_size // 2)
                candidates = [
                    normalized.rfind(separator, lower_bound, hard_end)
                    for separator in ("\n\n", "\n", "。", ". ", " ")
                ]
                boundary = max(candidates)
                if boundary >= lower_bound:
                    end = boundary + 1

            chunk = normalized[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(normalized):
                break
            start = max(start + 1, end - self._chunk_overlap)
        return chunks


class ParagraphTextSplitter(TextSplitter):
    """Structure-preserving baseline for Markdown/TXT/DOCX extracted text."""
    def __init__(self, *, chunk_size: int, chunk_overlap: int) -> None:
        self._fallback = CharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    def split(self, text: str) -> list[str]:
        paragraphs = [part.strip() for part in text.replace("\r\n", "\n").split("\n\n") if part.strip()]
        if not paragraphs:
            return []
        merged: list[str] = []
        current = ""
        for paragraph in paragraphs:
            candidate = f"{current}\n\n{paragraph}" if current else paragraph
            if len(candidate) > self._fallback._chunk_size and current:
                merged.append(current)
                current = paragraph
            else:
                current = candidate
        if current:
            merged.append(current)
        return [part for chunk in merged for part in (self._fallback.split(chunk) if len(chunk) > self._fallback._chunk_size else [chunk])]

