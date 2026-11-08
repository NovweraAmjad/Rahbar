"""Chroma-backed retrieval using the indexer's configured embedding model."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Protocol, cast

import httpx

from ..config import Settings
from ..embeddings import embed_query
from ..schemas import Passage

if TYPE_CHECKING:
    from chromadb.api import ClientAPI
    from chromadb.api.types import Where

_SUPERSEDED_FILTER: Where = {"superseded": False}
_CHROMA_TIMEOUT_SECONDS = 2.0


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
            return []
        embedding = (
            query_embedding
            if query_embedding is not None
            else embed_query(question, self.settings)
        )

        from chromadb.errors import ChromaError

        try:
            collection = self.client.get_collection(name=self.settings.collection)
            results = collection.query(
                query_embeddings=embedding,
                n_results=k,
                where=_SUPERSEDED_FILTER,
                include=["documents", "metadatas", "distances"],
            )
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
                )
            )
        return passages
