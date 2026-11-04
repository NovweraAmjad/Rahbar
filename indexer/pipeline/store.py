"""Stage 5: Store embedded chunks in Chroma."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

import chromadb
import httpx

from .config import IndexerSettings
from .models import Chunk

_BATCH_SIZE = 100


def get_client(settings: IndexerSettings) -> Any:
    """Create the configured thin HTTP client for the Chroma server."""
    from chromadb.config import Settings as ChromaSettings

    return chromadb.HttpClient(
        host=settings.chroma_host,
        port=settings.chroma_port,
        settings=ChromaSettings(anonymized_telemetry=False),
    )


def _normalize_metadata(metadata: Mapping[str, object]) -> dict[str, str | int | float | bool]:
    normalized: dict[str, str | int | float | bool] = {}
    for key, value in metadata.items():
        if isinstance(value, bool):
            normalized[str(key)] = value
        elif isinstance(value, int):
            normalized[str(key)] = value
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError(f"metadata field {key!r} must be finite")
            normalized[str(key)] = value
        elif isinstance(value, str):
            normalized[str(key)] = value
        else:
            normalized[str(key)] = json.dumps(
                value, ensure_ascii=False, sort_keys=True, default=str
            )
    return normalized


def _validate_records(
    chunks: list[Chunk], vectors: list[list[float]]
) -> tuple[list[Chunk], list[list[float]], int]:
    if len(chunks) != len(vectors):
        raise ValueError(
            f"chunk and embedding counts differ: {len(chunks)} chunks, {len(vectors)} vectors"
        )
    if not chunks:
        return [], [], 0

    unique_records: dict[str, tuple[Chunk, list[float]]] = {}
    dimensions: int | None = None
    for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
        if not isinstance(chunk.id, str) or not chunk.id.strip():
            raise ValueError(f"chunk at index {index} has an empty or invalid ID")
        if not isinstance(chunk.text, str) or not chunk.text.strip():
            raise ValueError(f"chunk {chunk.id!r} has empty or invalid text")
        if not isinstance(chunk.source, str) or not chunk.source.strip():
            raise ValueError(f"chunk {chunk.id!r} has no source filename")
        if isinstance(chunk.page, bool) or not isinstance(chunk.page, int) or chunk.page < 1:
            raise ValueError(f"chunk {chunk.id!r} has an invalid page number")
        if not isinstance(vector, list) or not vector:
            raise ValueError(f"chunk {chunk.id!r} has an empty or invalid embedding")

        normalized_vector: list[float] = []
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"chunk {chunk.id!r} has a non-numeric embedding value")
            try:
                number = float(value)
            except OverflowError as exc:
                raise ValueError(f"chunk {chunk.id!r} has an out-of-range embedding value") from exc
            if not math.isfinite(number):
                raise ValueError(f"chunk {chunk.id!r} has a non-finite embedding value")
            normalized_vector.append(number)

        if dimensions is None:
            dimensions = len(normalized_vector)
        elif len(normalized_vector) != dimensions:
            raise ValueError(
                f"chunk {chunk.id!r} has embedding dimension {len(normalized_vector)}; "
                f"expected {dimensions}"
            )
        unique_records[chunk.id] = (chunk, normalized_vector)

    records = list(unique_records.values())
    return (
        [record[0] for record in records],
        [record[1] for record in records],
        dimensions or 0,
    )


def _prepare_metadata(chunk: Chunk) -> dict[str, str | int | float | bool]:
    metadata = _normalize_metadata(chunk.metadata())
    for key in ("source", "page", "section", "doc_version", "superseded"):
        if key not in metadata:
            raise ValueError(f"chunk {chunk.id!r} is missing required metadata {key!r}")
    if (
        not isinstance(metadata["source"], str)
        or metadata["source"] != chunk.source
        or isinstance(metadata["page"], bool)
        or not isinstance(metadata["page"], int)
        or metadata["page"] != chunk.page
    ):
        raise ValueError(f"chunk {chunk.id!r} metadata does not match its citation fields")
    return metadata


def _collection(client: Any, settings: IndexerSettings, dimension: int) -> Any:
    metadata: dict[str, str | int | float | bool] = {
        "hnsw:space": "cosine",
        "embed_model": settings.embed_model,
        "dimension": dimension,
    }
    collection = client.get_or_create_collection(
        name=settings.collection,
        metadata=metadata,
        embedding_function=None,
    )

    current = collection.metadata or {}
    for key in ("embed_model", "dimension", "hnsw:space"):
        expected = metadata[key]
        if current.get(key) != expected:
            raise ValueError(
                f"Chroma collection {settings.collection!r} has {key}={current.get(key)!r}; "
                f"expected {expected!r}"
            )
    return collection


def store(
    chunks: list[Chunk],
    vectors: list[list[float]],
    settings: IndexerSettings,
    *,
    prune_stale: bool = False,
) -> int:
    """Upsert validated chunks in bounded batches and return the collection count.

    Repeated IDs use last-input-wins semantics. Collection metadata records the
    embedding model and vector dimension so consumers can detect incompatible indexes.
    When requested, stale IDs are deleted only after every replacement batch succeeds.
    """
    records, embeddings, dimension = _validate_records(chunks, vectors)
    if not records:
        return 0
    if not settings.embed_model.strip():
        raise ValueError("embedding model must not be empty")

    metadata = [_prepare_metadata(chunk) for chunk in records]
    try:
        collection = _collection(get_client(settings), settings, dimension)
        for offset in range(0, len(records), _BATCH_SIZE):
            end = offset + _BATCH_SIZE
            collection.upsert(
                ids=[chunk.id for chunk in records[offset:end]],
                documents=[chunk.text for chunk in records[offset:end]],
                embeddings=embeddings[offset:end],
                metadatas=metadata[offset:end],
            )
        if prune_stale:
            current_ids = {chunk.id for chunk in records}
            existing_ids: list[str] = []
            offset = 0
            while True:
                batch_ids = collection.get(
                    limit=_BATCH_SIZE,
                    offset=offset,
                    include=[],
                )["ids"]
                existing_ids.extend(batch_ids)
                if len(batch_ids) < _BATCH_SIZE:
                    break
                offset += len(batch_ids)

            stale_ids = sorted(set(existing_ids) - current_ids)
            for offset in range(0, len(stale_ids), _BATCH_SIZE):
                collection.delete(ids=stale_ids[offset : offset + _BATCH_SIZE])
        return int(collection.count())
    except (chromadb.errors.ChromaError, httpx.HTTPError, OSError) as exc:
        raise RuntimeError(
            f"Unable to store chunks in Chroma collection {settings.collection!r} "
            f"at {settings.chroma_host}:{settings.chroma_port}: {exc}"
        ) from exc
