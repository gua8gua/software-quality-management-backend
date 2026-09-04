"""Tiny OpenAI-compatible embedding server used for local end-to-end validation."""

import hashlib
import json
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DIMENSION = 1536


def embed(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    vector = [((digest[index % len(digest)] / 255.0) * 2.0) - 1.0 for index in range(DIMENSION)]
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector]


class EmbeddingHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path != "/v1/embeddings":
            self.send_error(404)
            return

        size = int(self.headers.get("Content-Length", "0"))
        payload: dict[str, Any] = json.loads(self.rfile.read(size))
        inputs = payload.get("input", [])
        if isinstance(inputs, str):
            inputs = [inputs]
        response = {
            "object": "list",
            "data": [
                {"object": "embedding", "index": index, "embedding": embed(text)}
                for index, text in enumerate(inputs)
            ],
            "model": payload.get("model", "mock-embedding"),
        }
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), EmbeddingHandler).serve_forever()
