"""Query embeddings shared by retrieval and semantic caching."""

from __future__ import annotations

import math

import httpx

from .config import Settings

_EMBED_TIMEOUT_SECONDS = 120.0


def _query_embedding(payload: object, model: str) -> list[float]:
    if not isinstance(payload, dict):
        raise RuntimeError(f"Ollama returned a malformed embedding response for {model!r}")

    vectors = payload.get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != 1:
        raise RuntimeError(f"Ollama returned an invalid embedding count for {model!r}")

    vector = vectors[0]
    if not isinstance(vector, list) or not vector:
        raise RuntimeError(f"Ollama returned an empty or malformed embedding for {model!r}")

    result: list[float] = []
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuntimeError(f"Ollama returned a non-numeric embedding value for {model!r}")
        try:
            number = float(value)
        except OverflowError as exc:
            raise RuntimeError(
                f"Ollama returned an out-of-range embedding value for {model!r}"
            ) from exc
        if not math.isfinite(number):
            raise RuntimeError(f"Ollama returned a non-finite embedding value for {model!r}")
        result.append(number)
    return result


def embed_query(question: str, settings: Settings) -> list[float]:
    """Embed a single question with the model shared by indexing and retrieval."""
    if not settings.embed_model.strip():
        raise ValueError("embedding model must not be empty")

    url = f"{settings.ollama_url.rstrip('/')}/api/embed"
    try:
        response = httpx.post(
            url,
            json={"model": settings.embed_model, "input": question},
            timeout=_EMBED_TIMEOUT_SECONDS,
        )
    except httpx.RequestError as exc:
        raise RuntimeError(
            f"Unable to connect to Ollama at {settings.ollama_url!r} "
            f"for model {settings.embed_model!r}: {exc}"
        ) from exc

    if response.is_error:
        raise RuntimeError(
            f"Ollama embedding API returned HTTP {response.status_code} "
            f"for model {settings.embed_model!r}"
        )
    try:
        return _query_embedding(response.json(), settings.embed_model)
    except (ValueError, httpx.DecodingError) as exc:
        raise RuntimeError(
            f"Ollama returned invalid JSON for model {settings.embed_model!r}"
        ) from exc
