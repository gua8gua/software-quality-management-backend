import asyncio
from typing import Any

from app.providers.rerankers.base import RerankerProvider


class QwenTransformersReranker(RerankerProvider):
    """Thin adapter around Qwen3-Reranker's official Transformers recipe."""

    name = "qwen_transformers"

    def __init__(
        self,
        model: str,
        *,
        revision: str = "main",
        device: str | None = None,
        batch_size: int = 4,
        max_length: int = 8192,
    ):
        self.model = model
        self.model_version = revision
        self.revision = revision
        self.device = device
        self.batch_size = batch_size
        self.max_length = max_length
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._prefix_tokens: list[int] = []
        self._suffix_tokens: list[int] = []

    def _load_tokenizer(self) -> Any:
        if self._tokenizer is None:
            try:
                from transformers import AutoTokenizer
            except ImportError as exc:  # pragma: no cover - depends on deployment extra
                raise RuntimeError(
                    "transformers is not installed; install the 'models' extra"
                ) from exc
            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model, revision=self.revision, padding_side="left"
            )
        prefix = (
            "<|im_start|>system\nJudge whether the Document meets the requirements based on "
            'the Query and the Instruct provided. Note that the answer can only be "yes" or '
            '"no".<|im_end|>\n<|im_start|>user\n'
        )
        suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
        if not self._prefix_tokens:
            self._prefix_tokens = self._tokenizer.encode(prefix, add_special_tokens=False)
        if not self._suffix_tokens:
            self._suffix_tokens = self._tokenizer.encode(suffix, add_special_tokens=False)
        return self._tokenizer

    def _load(self) -> tuple[Any, Any]:
        tokenizer = self._load_tokenizer()
        if self._model is not None:
            return tokenizer, self._model
        try:
            from transformers import AutoModelForCausalLM
        except ImportError as exc:  # pragma: no cover - depends on deployment extra
            raise RuntimeError("transformers is not installed; install the 'models' extra") from exc
        kwargs: dict[str, Any] = {"torch_dtype": "auto", "revision": self.revision}
        if self.device:
            kwargs["device_map"] = self.device
        self._model = AutoModelForCausalLM.from_pretrained(self.model, **kwargs).eval()
        return self._tokenizer, self._model

    @staticmethod
    def _pairs(query: str, documents: list[str], instruction: str) -> list[str]:
        return [
            f"<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {document}"
            for document in documents
        ]

    def _input_length_report(self, query: str, documents: list[str], instruction: str) -> dict:
        tokenizer = self._load_tokenizer()
        body_lengths = [
            len(ids)
            for ids in tokenizer(
                self._pairs(query, documents, instruction),
                padding=False,
                truncation=False,
                add_special_tokens=False,
            )["input_ids"]
        ]
        overhead = len(self._prefix_tokens) + len(self._suffix_tokens)
        lengths = [length + overhead for length in body_lengths]
        oversize = [index for index, length in enumerate(lengths) if length > self.max_length]
        return {
            "status": "oversize" if oversize else "ok",
            "model": self.model,
            "count": len(documents),
            "max_tokens": self.max_length,
            "observed_max_tokens": max(lengths, default=0),
            "oversize_indexes": oversize,
            "token_lengths": lengths,
        }

    async def input_length_report(
        self, query: str, documents: list[str], instruction: str
    ) -> dict:
        return await asyncio.to_thread(
            self._input_length_report, query, documents, instruction
        )

    def _score_sync(self, query: str, documents: list[str], instruction: str) -> list[float]:
        if not documents:
            return []
        import torch

        tokenizer, model = self._load()
        true_token = tokenizer.convert_tokens_to_ids("yes")
        false_token = tokenizer.convert_tokens_to_ids("no")
        results: list[float] = []
        for offset in range(0, len(documents), self.batch_size):
            batch = self._pairs(
                query, documents[offset : offset + self.batch_size], instruction
            )
            inputs = tokenizer(
                batch,
                padding=False,
                truncation=False,
                return_attention_mask=False,
            )
            limit = self.max_length - len(self._prefix_tokens) - len(self._suffix_tokens)
            oversize = [index for index, ids in enumerate(inputs["input_ids"]) if len(ids) > limit]
            if oversize:
                absolute = [offset + index for index in oversize]
                raise ValueError(
                    "reranker_input_oversize: silent truncation is disabled; "
                    f"indexes={absolute}, max_tokens={self.max_length}"
                )
            inputs["input_ids"] = [
                self._prefix_tokens + ids + self._suffix_tokens for ids in inputs["input_ids"]
            ]
            inputs = tokenizer.pad(inputs, padding=True, return_tensors="pt")
            inputs = {key: value.to(model.device) for key, value in inputs.items()}
            with torch.no_grad():
                logits = model(**inputs).logits[:, -1, :]
                pair = torch.stack([logits[:, false_token], logits[:, true_token]], dim=1)
                results.extend(torch.nn.functional.log_softmax(pair, dim=1)[:, 1].exp().tolist())
        return results

    async def score(self, query: str, documents: list[str], instruction: str) -> list[float]:
        return await asyncio.to_thread(self._score_sync, query, documents, instruction)
