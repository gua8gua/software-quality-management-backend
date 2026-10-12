"""Offline admission counting using DeepSeek's published V4 reference encoder.

This is a reference estimate, NOT a claim about the provider's exact billing
count. The upstream response's usage remains authoritative. No model weights,
inference, document rewriting, truncation or generated summaries are involved.
"""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from tokenizers import Tokenizer

from .encoding_dsv4 import encode_messages

REVISION = "7872f01b1d1fe23eabc4c98b48bffcef5a386062"


@lru_cache(maxsize=1)
def tokenizer() -> Tokenizer:
    return Tokenizer.from_file(str(Path(__file__).with_name("tokenizer.json")))


def count_chat_tokens(body: dict) -> int:
    messages = deepcopy(body.get("messages"))
    if not isinstance(messages, list) or not messages:
        raise ValueError("Expected nonempty chat messages")
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("Expected message object")
        if message.get("role") == "developer":
            message["role"] = "system"
        content = message.get("content")
        if isinstance(content, list):
            # The review workflow is text-only. Reject other modalities rather
            # than silently undercounting them or inventing an image tokenizer.
            if any(not isinstance(part, dict) or part.get("type") != "text"
                   or not isinstance(part.get("text"), str) for part in content):
                raise ValueError("Only text content is supported by review admission")
            message["content"] = "".join(part["text"] for part in content)
        elif content is None:
            message["content"] = ""
        elif not isinstance(content, str):
            raise ValueError("Invalid message text")
    # The upstream reference encoder explicitly attaches tool definitions to
    # system messages; it then encodes the real tool history and generation cue.
    if messages[0].get("role") != "system":
        messages.insert(0, {"role": "system", "content": ""})
    if body.get("tools"):
        messages[0]["tools"] = deepcopy(body["tools"])
    if body.get("response_format"):
        messages[0]["response_format"] = deepcopy(body["response_format"])
    encoded = encode_messages(messages, thinking_mode="chat", drop_thinking=True)
    return len(tokenizer().encode(encoded, add_special_tokens=False).ids)
