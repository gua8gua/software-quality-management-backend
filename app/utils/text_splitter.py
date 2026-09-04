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

