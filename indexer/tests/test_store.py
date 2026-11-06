from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
from chromadb.errors import InternalError

from pipeline.config import get_settings
from pipeline.models import Chunk
from pipeline.store import store


class FakeCollection:
    def __init__(self, metadata: dict[str, object] | None = None) -> None:
        self.metadata = metadata or {}
        self.rows: dict[str, tuple[str, list[float], dict[str, object]]] = {}
        self.batches: list[list[str]] = []
        self.fail_upsert: Exception | None = None
        self.fail_delete: Exception | None = None
        self.get_calls = 0
        self.delete_calls: list[list[str]] = []

    def upsert(
        self,
        *,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, object]],
    ) -> None:
        if self.fail_upsert is not None:
            raise self.fail_upsert
        self.batches.append(ids)
        for chunk_id, text, vector, metadata in zip(ids, documents, embeddings, metadatas):
            self.rows[chunk_id] = (text, vector, metadata)

    def count(self) -> int:
        return len(self.rows)

    def get(
        self, *, limit: int, offset: int, include: list[str]
    ) -> dict[str, list[str]]:
        assert include == []
        self.get_calls += 1
        return {"ids": list(self.rows)[offset : offset + limit]}

    def delete(self, *, ids: list[str]) -> None:
        if self.fail_delete is not None:
            raise self.fail_delete
        self.delete_calls.append(ids)
        for chunk_id in ids:
            self.rows.pop(chunk_id, None)


class FakeClient:
    def __init__(self, collection: FakeCollection | None = None) -> None:
        self.collection = collection
        self.request: tuple[str, dict[str, object], object] | None = None

    def get_or_create_collection(
        self,
        *,
        name: str,
        metadata: dict[str, object],
        embedding_function: object,
    ) -> FakeCollection:
        self.request = (name, metadata, embedding_function)
        if self.collection is None:
            self.collection = FakeCollection(metadata)
        return self.collection


def _settings():
    return replace(
        get_settings(),
        embed_model="nomic-embed-text",
        chroma_host="chroma.test",
        chroma_port=8123,
        collection="rahbar-test",
    )


def _chunk(
    index: int,
    *,
    source: str = "rules.pdf",
    page: int = 3,
    text: str | None = None,
) -> Chunk:
    return Chunk(
        id=f"rules:2026:{page}:{index}",
        text=text or f"Policy text {index}",
        source=source,
        section="Academic Warning",
        page=page,
        doc_version="2026",
        superseded=False,
    )


def _use_client(monkeypatch, client: FakeClient) -> None:
    monkeypatch.setattr("pipeline.store.get_client", lambda _settings: client)


def test_store_upserts_chunks_with_embeddings_and_citation_metadata(monkeypatch) -> None:
    client = FakeClient()
    _use_client(monkeypatch, client)
    chunks = [_chunk(1), _chunk(2, page=4)]
    vectors = [[0.1, 0.2], [0.3, 0.4]]

    count = store(chunks, vectors, _settings())

    assert count == 2
    assert client.request == (
        "rahbar-test",
        {"hnsw:space": "cosine", "embed_model": "nomic-embed-text", "dimension": 2},
        None,
    )
    assert client.collection is not None
    assert client.collection.rows[chunks[0].id] == (
        chunks[0].text,
        vectors[0],
        {
            "source": "rules.pdf",
            "section": "Academic Warning",
            "page": 3,
            "doc_version": "2026",
            "superseded": False,
        },
    )
    assert client.collection.rows[chunks[1].id][2]["page"] == 4


def test_store_inserts_in_bounded_batches(monkeypatch) -> None:
    client = FakeClient()
    _use_client(monkeypatch, client)
    chunks = [_chunk(index) for index in range(205)]
    vectors = [[float(index), 0.0] for index in range(205)]

    assert store(chunks, vectors, _settings()) == 205

    assert client.collection is not None
    assert [len(batch) for batch in client.collection.batches] == [100, 100, 5]


def test_store_empty_input_does_not_connect(monkeypatch) -> None:
    def unexpected_client(_settings):
        raise AssertionError("empty input must not connect to Chroma")

    monkeypatch.setattr("pipeline.store.get_client", unexpected_client)

    assert store([], [], _settings()) == 0


def test_store_rejects_empty_section_before_connecting(monkeypatch) -> None:
    chunk = _chunk(1)
    chunk.section = "  "

    def unexpected_client(_settings):
        raise AssertionError("invalid citation metadata must be rejected before connecting")

    monkeypatch.setattr("pipeline.store.get_client", unexpected_client)

    with pytest.raises(ValueError, match="empty or inconsistent section"):
        store([chunk], [[1.0]], _settings())


def test_get_client_uses_configured_chroma_address(monkeypatch) -> None:
    import chromadb
    import chromadb.config

    calls: list[tuple[str, int, object]] = []
    monkeypatch.setattr(
        chromadb,
        "HttpClient",
        lambda *, host, port, settings: calls.append((host, port, settings)) or "client",
    )

    from pipeline.store import get_client

    assert get_client(_settings()) == "client"
    assert len(calls) == 1
    host, port, client_settings = calls[0]
    assert (host, port) == ("chroma.test", 8123)
    assert isinstance(client_settings, chromadb.config.Settings)
    assert client_settings.anonymized_telemetry is False


def test_store_duplicate_ids_are_last_input_wins(monkeypatch) -> None:
    client = FakeClient()
    _use_client(monkeypatch, client)
    first = _chunk(1, text="Older text")
    duplicate = _chunk(1, text="Latest text")

    count = store([first, duplicate], [[1.0, 0.0], [0.0, 1.0]], _settings())

    assert count == 1
    assert client.collection is not None
    assert client.collection.rows[first.id] == (
        "Latest text",
        [0.0, 1.0],
        first.metadata(),
    )
    assert client.collection.batches == [[first.id]]


def test_store_prunes_stale_ids_after_successful_upserts(monkeypatch) -> None:
    collection = FakeCollection(
        {"embed_model": "nomic-embed-text", "dimension": 2, "hnsw:space": "cosine"}
    )
    collection.rows["stale-1"] = ("old", [0.0, 0.0], {})
    collection.rows["stale-2"] = ("old", [0.0, 0.0], {})
    client = FakeClient(collection)
    _use_client(monkeypatch, client)
    chunk = _chunk(1)

    count = store([chunk], [[1.0, 2.0]], _settings(), prune_stale=True)

    assert count == 1
    assert set(collection.rows) == {chunk.id}
    assert collection.delete_calls == [["stale-1", "stale-2"]]
    assert collection.get_calls == 1


def test_store_does_not_prune_stale_ids_when_an_upsert_fails(monkeypatch) -> None:
    collection = FakeCollection(
        {"embed_model": "nomic-embed-text", "dimension": 1, "hnsw:space": "cosine"}
    )
    collection.rows["stale"] = ("old", [0.0], {})
    collection.fail_upsert = InternalError("server error")
    client = FakeClient(collection)
    _use_client(monkeypatch, client)

    with pytest.raises(RuntimeError, match="Unable to store chunks"):
        store([_chunk(1)], [[1.0]], _settings(), prune_stale=True)

    assert "stale" in collection.rows
    assert collection.get_calls == 0
    assert collection.delete_calls == []


def test_store_normalizes_unsupported_metadata_values(monkeypatch) -> None:
    client = FakeClient()
    _use_client(monkeypatch, client)
    chunk = _chunk(1)
    original_metadata = chunk.metadata
    monkeypatch.setattr(
        chunk,
        "metadata",
        lambda: {**original_metadata(), "extra": {"campus": "North", "codes": [1, 2]}},
    )

    store([chunk], [[1.0]], _settings())

    assert client.collection is not None
    assert client.collection.rows[chunk.id][2]["extra"] == (
        '{"campus": "North", "codes": [1, 2]}'
    )
    assert client.collection.rows[chunk.id][2]["source"] == "rules.pdf"
    assert client.collection.rows[chunk.id][2]["page"] == 3


@pytest.mark.parametrize(
    ("chunks", "vectors", "message"),
    [
        ([_chunk(1)], [], "counts differ"),
        ([_chunk(1)], [[1.0, 2.0], [3.0]], "counts differ"),
        ([_chunk(1)], [[]], "empty or invalid embedding"),
        ([_chunk(1)], [[1.0, float("inf")]], "non-finite"),
        ([_chunk(1)], [[True]], "non-numeric"),
        ([_chunk(1), _chunk(2)], [[1.0, 2.0], [1.0]], "embedding dimension"),
        ([_chunk(1, page=0)], [[1.0]], "invalid page number"),
        ([_chunk(1, source="")], [[1.0]], "no source filename"),
        ([_chunk(1, text=" ")], [[1.0]], "empty or invalid text"),
    ],
)
def test_store_rejects_malformed_records_before_connecting(
    monkeypatch, chunks, vectors, message: str
) -> None:
    def unexpected_client(_settings):
        raise AssertionError("invalid input must be rejected before connecting")

    monkeypatch.setattr("pipeline.store.get_client", unexpected_client)

    with pytest.raises(ValueError, match=message):
        store(chunks, vectors, _settings())


@pytest.mark.parametrize(
    "collection_metadata",
    [
        {"embed_model": "different-model"},
        {"dimension": 3},
        {"hnsw:space": "l2"},
        {},
    ],
)
def test_store_rejects_incompatible_existing_collection(
    monkeypatch, collection_metadata: dict[str, object]
) -> None:
    client = FakeClient(FakeCollection(collection_metadata))
    _use_client(monkeypatch, client)

    with pytest.raises(ValueError, match="expected"):
        store([_chunk(1)], [[1.0, 2.0]], _settings())

    assert client.collection is not None
    assert not client.collection.rows


def test_store_wraps_chroma_write_failures_explicitly(monkeypatch) -> None:
    client = FakeClient(
        FakeCollection(
            {"embed_model": "nomic-embed-text", "dimension": 1, "hnsw:space": "cosine"}
        )
    )
    assert client.collection is not None
    client.collection.fail_upsert = httpx.ConnectError("connection refused")
    _use_client(monkeypatch, client)

    with pytest.raises(RuntimeError, match="Unable to store chunks in Chroma collection"):
        store([_chunk(1)], [[1.0]], _settings())


def test_store_wraps_chroma_server_errors_explicitly(monkeypatch) -> None:
    client = FakeClient(
        FakeCollection(
            {"embed_model": "nomic-embed-text", "dimension": 1, "hnsw:space": "cosine"}
        )
    )
    assert client.collection is not None
    client.collection.fail_upsert = InternalError("server error")
    _use_client(monkeypatch, client)

    with pytest.raises(RuntimeError, match="Unable to store chunks in Chroma collection"):
        store([_chunk(1)], [[1.0]], _settings())
