from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app import embeddings
from app.routes import health


@pytest.fixture()
def readiness_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, MagicMock, MagicMock, Mock]]:
    collection_response = MagicMock()
    collection_response.json.return_value = {
        "id": "test-collection-id",
        "metadata": {
            "embed_model": "nomic-embed-text",
            "dimension": 768,
            "hnsw:space": "cosine",
        },
    }
    count_response = MagicMock()
    count_response.json.return_value = 3
    chroma = MagicMock()
    chroma.__enter__.return_value = chroma
    chroma.get.side_effect = [collection_response, count_response]
    redis_client = Mock()

    monkeypatch.setenv("RETRIEVER", "chroma")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setattr(main, "get_semantic_cache", lambda _settings: None)
    monkeypatch.setattr(health, "embedding_dimension", lambda _model, _url: 768)
    monkeypatch.setattr(health.httpx, "Client", Mock(return_value=chroma))
    monkeypatch.setattr(health.Redis, "from_url", Mock(return_value=redis_client))

    with TestClient(main.app) as client:
        yield client, chroma, collection_response, redis_client


def test_readiness_is_healthy_when_dependencies_and_index_are_ready(
    readiness_client: tuple[TestClient, MagicMock, MagicMock, Mock],
) -> None:
    client, chroma, _, redis_client = readiness_client

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {
            "chroma": {"status": "ok"},
            "embedding_model": {"status": "ok"},
            "redis": {"status": "ok"},
        },
    }
    assert chroma.get.call_count == 2
    redis_client.ping.assert_called_once_with()
    redis_client.close.assert_called_once_with()


def test_readiness_reports_unavailable_chroma_without_exposing_exception(
    readiness_client: tuple[TestClient, MagicMock, MagicMock, Mock],
) -> None:
    client, chroma, _, _ = readiness_client
    chroma.get.side_effect = httpx.ReadTimeout(
        "secret connection detail",
        request=httpx.Request("GET", "http://chroma:8000"),
    )

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["chroma"] == {
        "status": "unavailable",
        "detail": "Chroma is unreachable or the configured collection is unavailable.",
    }
    assert "secret connection detail" not in response.text


def test_readiness_reports_empty_collection(
    readiness_client: tuple[TestClient, MagicMock, MagicMock, Mock],
) -> None:
    client, chroma, collection_response, _ = readiness_client
    count_response = MagicMock()
    count_response.json.return_value = 0
    chroma.get.side_effect = [collection_response, count_response]

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["chroma"]["detail"] == (
        "The configured Chroma collection has no indexed records."
    )


def test_readiness_reports_unavailable_redis(
    readiness_client: tuple[TestClient, MagicMock, MagicMock, Mock],
) -> None:
    client, _, _, redis_client = readiness_client
    redis_client.ping.side_effect = TimeoutError("redis://user:secret@redis")

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == {
        "status": "unavailable",
        "detail": "Redis is unreachable.",
    }
    assert "secret" not in response.text
    redis_client.close.assert_called_once_with()


def test_readiness_reports_embedding_metadata_mismatch(
    readiness_client: tuple[TestClient, MagicMock, MagicMock, Mock],
) -> None:
    client, chroma, collection_response, _ = readiness_client
    collection_response.json.return_value["metadata"]["embed_model"] = "different-model"

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["chroma"] == {
        "status": "incompatible",
        "detail": "The collection embedding model does not match EMBED_MODEL.",
    }


def test_readiness_reports_invalid_embedding_dimension(
    readiness_client: tuple[TestClient, MagicMock, MagicMock, Mock],
) -> None:
    client, _, collection_response, _ = readiness_client
    collection_response.json.return_value["metadata"]["dimension"] = 0

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["chroma"] == {
        "status": "incompatible",
        "detail": "The collection embedding dimension metadata is missing or invalid.",
    }


def test_readiness_reports_valid_but_incompatible_embedding_dimension(
    readiness_client: tuple[TestClient, MagicMock, MagicMock, Mock],
) -> None:
    client, _, collection_response, _ = readiness_client
    collection_response.json.return_value["metadata"]["dimension"] = 1024

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["chroma"] == {
        "status": "incompatible",
        "detail": "The collection embedding dimension does not match the configured model.",
    }
    assert response.json()["checks"]["embedding_model"] == {"status": "incompatible"}


def test_embedding_dimension_uses_cached_ollama_model_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = Mock()
    response.json.return_value = {
        "model_info": {
            "general.architecture": "nomic-bert",
            "nomic-bert.embedding_length": 768,
        }
    }
    post = Mock(return_value=response)
    monkeypatch.setattr(embeddings.httpx, "post", post)
    model = "readiness-test-model"
    ollama_url = "http://ollama-readiness-test:11434"

    assert embeddings.embedding_dimension(model, ollama_url) == 768
    assert embeddings.embedding_dimension(model, ollama_url) == 768

    post.assert_called_once_with(
        f"{ollama_url}/api/show",
        json={"model": model},
        timeout=5.0,
    )


def test_liveness_remains_independent_of_dependency_failures(
    readiness_client: tuple[TestClient, MagicMock, MagicMock, Mock],
) -> None:
    client, chroma, _, redis_client = readiness_client
    chroma.get.side_effect = RuntimeError("Chroma is down")
    redis_client.ping.side_effect = TimeoutError("Redis is down")

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}
    chroma.get.assert_not_called()
    redis_client.ping.assert_not_called()
