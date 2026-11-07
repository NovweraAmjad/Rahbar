"""Query embeddings shared by retrieval and semantic caching."""

from __future__ import annotations

import math
import threading
import time

import httpx

from .config import Settings

_EMBED_TIMEOUT_SECONDS = 120.0
_MODEL_INFO_TIMEOUT_SECONDS = 5.0
_MODEL_INFO_CACHE_TTL_SECONDS = 300.0
_model_info_lock = threading.Lock()
_model_dimensions: dict[tuple[str, str], tuple[float, int]] = {}


def embedding_dimension(model: str, ollama_url: str) -> int:
    """Read and briefly cache the configured Ollama model's embedding length."""
    if not model.strip():
        raise ValueError("embedding model must not be empty")

    cache_key = (ollama_url.rstrip("/"), model)
    now = time.monotonic()
    with _model_info_lock:
        cached = _model_dimensions.get(cache_key)
        if cached is not None and cached[0] > now:
            return cached[1]

        response = httpx.post(
            f"{cache_key[0]}/api/show",
            json={"model": model},
            timeout=_MODEL_INFO_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except (ValueError, httpx.DecodingError) as exc:
            raise RuntimeError("Ollama returned invalid model metadata") from exc

        if not isinstance(payload, dict):
            raise RuntimeError("Ollama returned invalid model metadata")
        model_info = payload.get("model_info")
        if not isinstance(model_info, dict):
            raise RuntimeError("Ollama model metadata has no embedding architecture")
        architecture = model_info.get("general.architecture")
        if not isinstance(architecture, str) or not architecture:
            raise RuntimeError("Ollama model metadata has no embedding architecture")

        dimension = model_info.get(f"{architecture}.embedding_length")
        if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0:
            raise RuntimeError("Ollama model metadata has no valid embedding length")

        _model_dimensions[cache_key] = (
            time.monotonic() + _MODEL_INFO_CACHE_TTL_SECONDS,
            dimension,
        )
        return dimension


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
