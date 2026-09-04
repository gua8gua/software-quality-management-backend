from abc import ABC, abstractmethod


class DocumentParser(ABC):
    @abstractmethod
    async def parse(self, content: str, *, source_type: str = "text") -> str:
        """Extract normalized text from an input document."""

