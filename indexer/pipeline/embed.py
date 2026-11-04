"""Stage 4: Embed chunks using the configured Ollama model."""

from __future__ import annotations

import math

import httpx

from .config import IndexerSettings

_BATCH_SIZE = 32
_TIMEOUT_SECONDS = 120.0


def _embedding_vectors(
    payload: object, model: str, expected_count: int
) -> list[list[float]]:
    if not isinstance(payload, dict):
        raise RuntimeError(f"Ollama returned a malformed response for model {model!r}")

    raw_vectors = payload.get("embeddings")
    if not isinstance(raw_vectors, list) or len(raw_vectors) != expected_count:
        raise RuntimeError(
            f"Ollama returned an invalid embedding count for model {model!r}: "
            f"expected {expected_count}"
        )

    vectors: list[list[float]] = []
    dimensions: int | None = None
    for vector in raw_vectors:
        if not isinstance(vector, list) or not vector:
            raise RuntimeError(f"Ollama returned an empty or malformed vector for {model!r}")
        normalized: list[float] = []
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise RuntimeError(
                    f"Ollama returned non-numeric embedding values for {model!r}"
                )
            try:
                number = float(value)
            except OverflowError as exc:
                raise RuntimeError(
                    f"Ollama returned out-of-range embedding values for {model!r}"
                ) from exc
            if not math.isfinite(number):
                raise RuntimeError(
                    f"Ollama returned non-finite embedding values for {model!r}"
                )
            normalized.append(number)
        if dimensions is None:
            dimensions = len(vector)
        elif len(vector) != dimensions:
            raise RuntimeError(f"Ollama returned inconsistent embedding dimensions for {model!r}")
        vectors.append(normalized)
    return vectors


def embed(texts: list[str], settings: IndexerSettings) -> list[list[float]]:
    """Return one validated Ollama embedding per input text, preserving input order.

    Requests are sent in batches of 32 using Ollama's ``/api/embed`` endpoint.
    No fallback vectors are generated if a request or response is invalid.
    """
    if not texts:
        return []
    if not settings.embed_model.strip():
        raise ValueError("embedding model must not be empty")

    url = f"{settings.ollama_url.rstrip('/')}/api/embed"
    vectors: list[list[float]] = []
    dimensions: int | None = None

    for offset in range(0, len(texts), _BATCH_SIZE):
        batch = texts[offset : offset + _BATCH_SIZE]
        try:
            response = httpx.post(
                url,
                json={"model": settings.embed_model, "input": batch},
                timeout=_TIMEOUT_SECONDS,
            )
        except httpx.RequestError as exc:
            raise RuntimeError(
                f"Unable to connect to Ollama at {settings.ollama_url!r} "
                f"for model {settings.embed_model!r}: {exc}"
            ) from exc

        if response.is_error:
            detail = response.text[:500]
            raise RuntimeError(
                f"Ollama embedding API returned HTTP {response.status_code} "
                f"for model {settings.embed_model!r}: {detail}"
            )

        try:
            payload = response.json()
        except (ValueError, httpx.DecodingError) as exc:
            raise RuntimeError(
                f"Ollama returned invalid JSON for model {settings.embed_model!r}"
            ) from exc

        batch_vectors = _embedding_vectors(payload, settings.embed_model, len(batch))
        batch_dimensions = len(batch_vectors[0])
        if dimensions is None:
            dimensions = batch_dimensions
        elif batch_dimensions != dimensions:
            raise RuntimeError(
                f"Ollama returned inconsistent embedding dimensions for "
                f"model {settings.embed_model!r}"
            )
        vectors.extend(batch_vectors)

    return vectors
