"""Deterministic Ollama-compatible embedding endpoint for isolated CI fixtures."""

from __future__ import annotations

from hashlib import blake2b
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import re
from typing import Any

_DIMENSIONS = 2048
_MODEL = "rahbar-fixture-hash-embed-v1"
_STOP_WORDS = frozenset(
    {
        "a",
        "about",
        "after",
        "all",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "before",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "if",
        "in",
        "ignore",
        "instructions",
        "is",
        "it",
        "earlier",
        "now",
        "optional",
        "of",
        "on",
        "or",
        "previous",
        "rules",
        "say",
        "the",
        "tell",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "with",
    }
)


def _vector(text: str) -> list[float]:
    tokens: list[str] = []
    for token in re.findall(r"[a-z0-9]+", text.casefold()):
        if token in _STOP_WORDS:
            continue
        if token.endswith("ance") and token[:-4].endswith("d"):
            token = token[:-4]
        tokens.append(token)
    features = tokens + [f"{left}_{right}" for left, right in zip(tokens, tokens[1:])]
    vector = [0.0] * _DIMENSIONS
    for feature in features:
        digest = blake2b(feature.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest, "big") % _DIMENSIONS
        vector[index] += 1.0
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude:
        vector = [value / magnitude for value in vector]
    return vector


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/healthz":
            self.send_error(404)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok\n")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body: Any = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_error(400)
            return

        if not isinstance(body, dict) or body.get("model") != _MODEL:
            self.send_error(400)
            return
        if self.path == "/api/show":
            response: Any = {
                "model_info": {
                    "general.architecture": "rahbar_fixture",
                    "rahbar_fixture.embedding_length": _DIMENSIONS,
                }
            }
        elif self.path == "/api/embed":
            inputs = body.get("input")
            if isinstance(inputs, str):
                inputs = [inputs]
            if (
                not isinstance(inputs, list)
                or not inputs
                or any(not isinstance(item, str) for item in inputs)
            ):
                self.send_error(400)
                return
            response = {"embeddings": [_vector(item) for item in inputs]}
        else:
            self.send_error(404)
            return

        encoded = json.dumps(response, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8081), _Handler).serve_forever()
