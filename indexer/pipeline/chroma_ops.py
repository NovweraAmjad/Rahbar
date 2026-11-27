"""Chroma collection management: delete chunks, query by metadata, mark superseded."""

from __future__ import annotations

from typing import Any

import chromadb
import httpx

from .config import IndexerSettings

_BATCH_SIZE = 100


def get_collection(settings: IndexerSettings) -> Any:
    """Get the Chroma collection for this configuration."""
    from chromadb.config import Settings as ChromaSettings

    client = chromadb.HttpClient(
        host=settings.chroma_host,
        port=settings.chroma_port,
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    return client.get_or_create_collection(
        name=settings.collection,
        embedding_function=None,
    )


def get_chunks_by_source(
    settings: IndexerSettings, source: str
) -> dict[str, Any]:
    """
    Retrieve all chunks from Chroma for a given source document.

    Returns: {chunk_id: metadata_dict}
    """
    try:
        collection = get_collection(settings)
        result = collection.get(
            where={"source": source},
            include=["metadatas"],
        )
        chunks = {}
        for chunk_id, metadata in zip(result["ids"], result["metadatas"] or []):
            if isinstance(metadata, dict):
                chunks[chunk_id] = metadata
        return chunks
    except (chromadb.errors.ChromaError, httpx.HTTPError, OSError) as exc:
        raise RuntimeError(
            f"Unable to read chunks for {source!r} from Chroma collection "
            f"{settings.collection!r}: {exc}"
        ) from exc


def delete_chunks_by_ids(
    settings: IndexerSettings, chunk_ids: list[str]
) -> int:
    """
    Delete chunks from Chroma by their IDs.

    Returns: count of deleted chunks
    """
    if not chunk_ids:
        return 0

    try:
        collection = get_collection(settings)
        deleted = 0
        for offset in range(0, len(chunk_ids), _BATCH_SIZE):
            batch = chunk_ids[offset : offset + _BATCH_SIZE]
            collection.delete(ids=batch)
            deleted += len(batch)
        return deleted
    except (chromadb.errors.ChromaError, httpx.HTTPError, OSError) as exc:
        raise RuntimeError(
            f"Failed to delete chunks from Chroma collection {settings.collection!r}: {exc}"
        ) from exc


def mark_superseded(
    settings: IndexerSettings, chunk_ids: list[str]
) -> int:
    """
    Mark chunks as superseded by updating their metadata.

    Returns: count of updated chunks
    """
    if not chunk_ids:
        return 0

    try:
        collection = get_collection(settings)
        updated = 0
        for offset in range(0, len(chunk_ids), _BATCH_SIZE):
            batch = chunk_ids[offset : offset + _BATCH_SIZE]
            batch_result = collection.get(ids=batch, include=["metadatas"])
            ids = batch_result["ids"]
            metadatas = batch_result["metadatas"] or []
            updated_metadata: list[dict[str, Any]] = []
            updated_ids: list[str] = []
            for chunk_id, metadata in zip(ids, metadatas):
                if isinstance(metadata, dict):
                    updated_ids.append(chunk_id)
                    updated_metadata.append({**metadata, "superseded": True})
            if updated_ids:
                collection.update(ids=updated_ids, metadatas=updated_metadata)
                updated += len(updated_ids)
        return updated
    except (chromadb.errors.ChromaError, httpx.HTTPError, OSError) as exc:
        raise RuntimeError(
            f"Failed to mark chunks as superseded in Chroma collection {settings.collection!r}: {exc}"
        ) from exc


def get_all_chunks_by_source(settings: IndexerSettings) -> dict[str, list[str]]:
    """
    Retrieve all chunks in the collection grouped by source document.

    Returns: {source: [chunk_id, ...]}
    """
    try:
        collection = get_collection(settings)
        chunks_by_source: dict[str, list[str]] = {}
        
        offset = 0
        while True:
            batch = collection.get(
                limit=_BATCH_SIZE,
                offset=offset,
                include=["metadatas"],
            )
            if not batch["ids"]:
                break
            
            for chunk_id, metadata in zip(batch["ids"], batch["metadatas"] or []):
                if isinstance(metadata, dict):
                    source = metadata.get("source", "unknown")
                    if source not in chunks_by_source:
                        chunks_by_source[source] = []
                    chunks_by_source[source].append(chunk_id)
            
            if len(batch["ids"]) < _BATCH_SIZE:
                break
            offset += len(batch["ids"])
        
        return chunks_by_source
    except (chromadb.errors.ChromaError, httpx.HTTPError, OSError) as exc:
        raise RuntimeError(
            f"Unable to list chunks from Chroma collection "
            f"{settings.collection!r}: {exc}"
        ) from exc


def verify_no_orphans(
    settings: IndexerSettings, indexed_sources: set[str]
) -> dict[str, Any]:
    """
    Verify that there are no orphan chunks in Chroma.

    Orphans are chunks from sources that are not currently indexed.

    Returns: {
        "has_orphans": bool,
        "orphan_count": int,
        "orphan_sources": [source, ...],
        "chunks_by_source": {source: count, ...},
    }
    """
    chunks_by_source = get_all_chunks_by_source(settings)
    orphan_sources = [s for s in chunks_by_source.keys() if s not in indexed_sources]
    orphan_count = sum(len(ids) for s, ids in chunks_by_source.items() if s in orphan_sources)
    
    return {
        "has_orphans": len(orphan_sources) > 0,
        "orphan_count": orphan_count,
        "orphan_sources": orphan_sources,
        "chunks_by_source": {s: len(ids) for s, ids in chunks_by_source.items()},
    }
