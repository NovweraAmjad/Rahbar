"""Chroma-backed retrieval using the indexer's configured embedding model."""

from __future__ import annotations

import math
from time import perf_counter
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID

import httpx

from ..config import Settings
from ..embeddings import embed_query
from ..schemas import Passage
from .base import RetrievalResults

if TYPE_CHECKING:
    from chromadb.api import ClientAPI
    from chromadb.api.types import Where

_SUPERSEDED_FILTER: Where = {"superseded": False}
_CHROMA_TIMEOUT_SECONDS = 2.0
def _requested_versions(question: str) -> list[str]:
    import re

    normalized = []
    for match in re.findall(r"(?<!\d)((?:19|20)\d{2}(?:[-/]\d{2,4})?)", question):
        value = match.replace("/", "-")
        if value not in normalized:
            normalized.append(value)
    return normalized


def _version_match(version: str | None, requested: str) -> bool:
    if not version:
        return False
    normalized = version.strip().replace("/", "-")
    if normalized == requested:
        return True
    return normalized.startswith(requested[:4]) and len(requested) == 4


def _rerank_for_versions(question: str, passages: list[Passage], k: int) -> list[Passage]:
    requested = _requested_versions(question)
    if not requested:
        return passages[:k]

    ranked: list[tuple[float, Passage]] = []
    for passage in passages:
        exact = sum(_version_match(passage.doc_version, version) for version in requested)
        score = passage.score
        if exact:
            score += 0.25
        elif passage.doc_version:
            score -= 0.05
        ranked.append((score, passage))

    ranked.sort(key=lambda item: item[0], reverse=True)
    selected: list[Passage] = []
    selected_ids: set[str] = set()

    if len(requested) > 1:
        for version in requested:
            match = next(
                (
                    (score, passage)
                    for score, passage in ranked
                    if _version_match(passage.doc_version, version)
                ),
                None,
            )
            if match is not None and match[1].chunk_id not in selected_ids:
                selected.append(match[1])
                selected_ids.add(match[1].chunk_id)

    for _score, passage in ranked:
        if passage.chunk_id not in selected_ids:
            selected.append(passage)
            selected_ids.add(passage.chunk_id)
        if len(selected) >= k:
            break
    return selected[:k]


class _ChromaHttpSession(Protocol):
    timeout: httpx.Timeout | float | None


class _ChromaHttpServer(Protocol):
    _session: _ChromaHttpSession


class _ChromaHttpClient(Protocol):
    _server: _ChromaHttpServer


def _result_rows(response: object) -> list[tuple[str, str, dict[str, object], float]]:
    if not isinstance(response, dict):
        raise RuntimeError("Chroma returned a malformed query response")

    rows: dict[str, list[object]] = {}
    for key in ("ids", "documents", "metadatas", "distances"):
        value = response.get(key)
        if not isinstance(value, list) or not value or not isinstance(value[0], list):
            raise RuntimeError(f"Chroma returned malformed {key!r} in query response")
        rows[key] = value[0]

    ids = rows["ids"]
    documents = rows["documents"]
    metadatas = rows["metadatas"]
    distances = rows["distances"]
    if not ids:
        return []
    if not (len(ids) == len(documents) == len(metadatas) == len(distances)):
        raise RuntimeError("Chroma returned inconsistent result lengths")

    results: list[tuple[str, str, dict[str, object], float]] = []
    for chunk_id, document, metadata, distance in zip(ids, documents, metadatas, distances):
        if not isinstance(chunk_id, str) or not chunk_id:
            raise RuntimeError("Chroma returned an invalid chunk ID")
        if not isinstance(document, str):
            raise RuntimeError(f"Chroma returned an invalid document for chunk {chunk_id!r}")
        if not isinstance(metadata, dict):
            raise RuntimeError(f"Chroma returned invalid metadata for chunk {chunk_id!r}")
        if isinstance(distance, bool) or not isinstance(distance, (int, float)):
            raise RuntimeError(f"Chroma returned an invalid cosine distance for chunk {chunk_id!r}")
        normalized_distance = float(distance)
        if not math.isfinite(normalized_distance):
            raise RuntimeError(f"Chroma returned a non-finite cosine distance for {chunk_id!r}")
        results.append((chunk_id, document, cast(dict[str, object], metadata), normalized_distance))
    return results


class ChromaRetriever:
    name = "chroma"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: ClientAPI | None = None

    @property
    def client(self) -> ClientAPI:
        if self._client is None:
            import chromadb  # the thin client, from chromadb-client in requirements.txt
            from chromadb.config import Settings as ChromaSettings

            client = chromadb.HttpClient(
                host=self.settings.chroma_host,
                port=self.settings.chroma_port,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            cast(_ChromaHttpClient, client)._server._session.timeout = httpx.Timeout(
                _CHROMA_TIMEOUT_SECONDS
            )
            self._client = client
        return self._client

    def retrieve(
        self,
        question: str,
        k: int,
        query_embedding: list[float] | None = None,
    ) -> list[Passage]:
        if k < 0:
            raise ValueError("k must not be negative")
        if k == 0:
            return RetrievalResults([], {
                "filters_applied": dict(_SUPERSEDED_FILTER),
                "raw_ranked_order": [],
                "reranked_order": [],
                "embedding_latency_ms": 0.0,
                "vector_query_latency_ms": 0.0,
                "reranking_latency_ms": 0.0,
                "collection_version": None,
            })
        embedding_started = perf_counter()
        embedding = (
            query_embedding
            if query_embedding is not None
            else embed_query(question, self.settings)
        )
        embedding_latency_ms = (
            0.0 if query_embedding is not None
            else (perf_counter() - embedding_started) * 1000
        )

        from chromadb.errors import ChromaError

        try:
            query_started = perf_counter()
            collection = self.client.get_collection(name=self.settings.collection)
            candidate_count = max(k, k * 3)
            results = collection.query(
                query_embeddings=embedding,
                n_results=candidate_count,
                where=_SUPERSEDED_FILTER,
                include=["documents", "metadatas", "distances"],
            )
            vector_query_latency_ms = (perf_counter() - query_started) * 1000
        except (ChromaError, OSError) as exc:
            raise RuntimeError(
                f"Unable to query Chroma collection {self.settings.collection!r} "
                f"at {self.settings.chroma_host}:{self.settings.chroma_port}: {exc}"
            ) from exc

        passages: list[Passage] = []
        for chunk_id, text, metadata, distance in _result_rows(results):
            source = metadata.get("source")
            section = metadata.get("section")
            page = metadata.get("page")
            doc_version = metadata.get("doc_version")
            superseded = metadata.get("superseded")
            document_id = metadata.get("document_id", metadata.get("doc_id"))

            if not isinstance(source, str) or not source.strip():
                raise RuntimeError(f"Chroma metadata for {chunk_id!r} has no valid source")
            if not isinstance(section, str) or not section.strip():
                raise RuntimeError(f"Chroma metadata for {chunk_id!r} has no valid section")
            if isinstance(page, bool) or (page is not None and not isinstance(page, int)):
                raise RuntimeError(f"Chroma metadata for {chunk_id!r} has an invalid page")
            if not isinstance(doc_version, str) or not doc_version:
                raise RuntimeError(f"Chroma metadata for {chunk_id!r} has no valid doc_version")
            if not isinstance(superseded, bool):
                raise RuntimeError(f"Chroma metadata for {chunk_id!r} has no valid superseded flag")
            if document_id is not None and (
                not isinstance(document_id, str) or not document_id.strip()
            ):
                raise RuntimeError(f"Chroma metadata for {chunk_id!r} has an invalid document ID")
            if superseded:
                continue

            passages.append(
                Passage(
                    chunk_id=chunk_id,
                    text=text,
                    source=source,
                    section=section,
                    page=page,
                    score=1.0 - distance,
                    doc_version=doc_version,
                    superseded=superseded,
                    document_id=document_id,
                )
            )
        raw_ranked_order = [passage.chunk_id for passage in passages]
        reranking_started = perf_counter()
        reranked = _rerank_for_versions(question, passages, k)
        reranking_latency_ms = (perf_counter() - reranking_started) * 1000
        raw_collection_metadata = collection.metadata
        collection_metadata = (
            raw_collection_metadata if isinstance(raw_collection_metadata, dict) else {}
        )
        collection_version = collection_metadata.get(
            "collection_version", collection_metadata.get("version")
        )
        collection_id = getattr(collection, "id", None)
        if isinstance(collection_id, UUID):
            collection_id = str(collection_id)
        if not isinstance(collection_id, (str, int, float, bool)):
            collection_id = None
        collection_version_source = "collection_metadata"
        if collection_version is None:
            collection_version = collection_id
            collection_version_source = "collection_id"
        return RetrievalResults(
            reranked,
            {
                "filters_applied": dict(_SUPERSEDED_FILTER),
                "candidate_count": candidate_count,
                "raw_ranked_order": raw_ranked_order,
                "reranked_order": [passage.chunk_id for passage in reranked],
                "embedding_latency_ms": embedding_latency_ms,
                "vector_query_latency_ms": vector_query_latency_ms,
                "reranking_latency_ms": reranking_latency_ms,
                "collection_version": collection_version,
                "collection_version_source": collection_version_source,
                "collection_id": collection_id,
                "collection_metadata": {
                    key: value
                    for key, value in collection_metadata.items()
                    if isinstance(value, (str, int, float, bool))
                },
            },
        )
