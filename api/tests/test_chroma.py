from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import httpx
import pytest

from app.config import Settings
from app import embeddings
from app.retrieval.chroma import ChromaRetriever


def _settings() -> Settings:
    return Settings(
        embed_model="nomic-embed-text",
        ollama_url="http://ollama:11434/",
        chroma_host="chroma-test",
        chroma_port=8010,
        collection="rahbar-test",
    )


def _query_response(
    *,
    ids: list[str] | None = None,
    documents: list[str] | None = None,
    metadatas: list[dict[str, object]] | None = None,
    distances: list[float] | None = None,
) -> dict[str, list[list[Any]]]:
    return {
        "ids": [ids or []],
        "documents": [documents or []],
        "metadatas": [metadatas or []],
        "distances": [distances or []],
    }


def _retriever(
    monkeypatch: pytest.MonkeyPatch,
    result: object | None = None,
    *,
    chroma_error: Exception | None = None,
) -> tuple[ChromaRetriever, Mock, Mock, Mock]:
    import chromadb

    collection = Mock()
    collection.query.return_value = result or _query_response()
    if chroma_error is not None:
        collection.query.side_effect = chroma_error
    client = Mock()
    client.get_collection.return_value = collection
    client_factory = Mock(return_value=client)
    monkeypatch.setattr(chromadb, "HttpClient", client_factory)

    embedding_response = Mock()
    embedding_response.is_error = False
    embedding_response.json.return_value = {"embeddings": [[0.1, 0.2, 0.3]]}
    post = Mock(return_value=embedding_response)
    monkeypatch.setattr(embeddings.httpx, "post", post)

    return ChromaRetriever(_settings()), client_factory, collection, post


def test_retrieve_embeds_with_configured_model_and_maps_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _query_response(
        ids=["chunk-current", "chunk-old"],
        documents=["Current policy text", "Superseded policy text"],
        metadatas=[
            {
                "source": "policy-2026.pdf",
                "section": "4.2 Registration",
                "page": 12,
                "doc_version": "2026-06",
                "superseded": False,
            },
            {
                "source": "policy-2023.pdf",
                "section": "4.2 Registration",
                "page": 10,
                "doc_version": "2023-08",
                "superseded": True,
            },
        ],
        distances=[0.12, 0.4],
    )
    retriever, client_factory, collection, post = _retriever(monkeypatch, result)

    passages = retriever.retrieve("What are the registration rules?", k=4)

    post.assert_called_once_with(
        "http://ollama:11434/api/embed",
        json={
            "model": "nomic-embed-text",
            "input": "What are the registration rules?",
        },
        timeout=120.0,
    )
    client_factory.assert_called_once()
    client = client_factory.return_value
    client.get_collection.assert_called_once_with(name="rahbar-test")
    collection.query.assert_called_once_with(
        query_embeddings=[0.1, 0.2, 0.3],
        n_results=4,
        where={"superseded": False},
        include=["documents", "metadatas", "distances"],
    )
    assert len(passages) == 1
    passage = passages[0]
    assert passage.chunk_id == "chunk-current"
    assert passage.text == "Current policy text"
    assert passage.source == "policy-2026.pdf"
    assert passage.section == "4.2 Registration"
    assert passage.page == 12
    assert passage.doc_version == "2026-06"
    assert passage.superseded is False
    assert passage.score == pytest.approx(0.88)


def test_retrieve_returns_empty_list_for_no_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    retriever, _, _, _ = _retriever(monkeypatch, _query_response())

    assert retriever.retrieve("Question with no matches", k=5) == []


@pytest.mark.parametrize("section", [None, "", "   "])
def test_retrieve_rejects_missing_or_blank_section_metadata(
    monkeypatch: pytest.MonkeyPatch,
    section: str | None,
) -> None:
    metadata: dict[str, object] = {
        "source": "policy-2026.pdf",
        "page": 10,
        "doc_version": "2026-06",
        "superseded": False,
    }
    if section is not None:
        metadata["section"] = section
    retriever, _, _, _ = _retriever(
        monkeypatch,
        _query_response(
            ids=["policy:2026:10:1"],
            documents=["A policy clause."],
            metadatas=[metadata],
            distances=[0.1],
        ),
    )

    with pytest.raises(RuntimeError, match="has no valid section"):
        retriever.retrieve("Question about policy", k=3)


def test_retrieve_returns_empty_without_network_for_zero_k(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retriever, _, _, post = _retriever(monkeypatch)

    assert retriever.retrieve("Question with no requested matches", k=0) == []
    post.assert_not_called()


@pytest.mark.parametrize(
    ("embedding_payload", "expected_message"),
    [
        ({"embeddings": []}, "embedding count"),
        ({"embeddings": [["not-a-number"]]}, "non-numeric"),
    ],
)
def test_retrieve_rejects_malformed_embedding(
    monkeypatch: pytest.MonkeyPatch,
    embedding_payload: dict[str, object],
    expected_message: str,
) -> None:
    retriever, _, _, post = _retriever(monkeypatch)
    post.return_value.json.return_value = embedding_payload

    with pytest.raises(RuntimeError, match=expected_message):
        retriever.retrieve("Question with malformed vector", k=3)


def test_retrieve_reports_ollama_connection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    retriever, _, _, post = _retriever(monkeypatch)
    post.side_effect = httpx.ConnectError(
        "connection refused",
        request=httpx.Request("POST", "http://ollama:11434/api/embed"),
    )

    with pytest.raises(RuntimeError, match="Unable to connect to Ollama"):
        retriever.retrieve("Question during outage", k=3)


def test_retrieve_reports_chroma_connection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    retriever, _, _, _ = _retriever(monkeypatch, chroma_error=OSError("connection refused"))

    with pytest.raises(RuntimeError, match="Unable to query Chroma collection"):
        retriever.retrieve("Question during outage", k=3)


def test_retrieve_rejects_malformed_chroma_response(monkeypatch: pytest.MonkeyPatch) -> None:
    retriever, _, _, _ = _retriever(
        monkeypatch,
        {"ids": [[]], "documents": [[]], "metadatas": [[]]},
    )

    with pytest.raises(RuntimeError, match="'distances'"):
        retriever.retrieve("Question with malformed results", k=3)
