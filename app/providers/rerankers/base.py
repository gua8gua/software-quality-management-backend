from abc import ABC, abstractmethod


class RerankerProvider(ABC):
    name: str = "base"
    model: str = ""
    model_version: str = ""

    async def input_length_report(
        self, query: str, documents: list[str], instruction: str
    ) -> dict:
        return {
            "status": "tokenizer_unavailable",
            "model": self.model,
            "count": len(documents),
            "max_tokens": None,
            "oversize_indexes": [],
        }

    @abstractmethod
    async def score(self, query: str, documents: list[str], instruction: str) -> list[float]:
        raise NotImplementedError
