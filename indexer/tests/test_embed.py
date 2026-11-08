from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from pipeline.config import get_settings
from pipeline.embed import embed
from pipeline.models import Chunk


def _settings():
    return replace(
        get_settings(),
        embed_model="nomic-embed-text",
        ollama_url="http://ollama.test:11434/",
    )


def _response(vectors: list[list[float]]) -> httpx.Response:
    return httpx.Response(200, json={"embeddings": vectors})


def test_embed_posts_configured_model_and_returns_vectors(monkeypatch) -> None:
    requests: list[tuple[str, dict[str, object], float]] = []

    def fake_post(url: str, *, json: dict[str, object], timeout: float) -> httpx.Response:
        requests.append((url, json, timeout))
        return _response([[0.1, 0.2], [0.3, 0.4]])

    monkeypatch.setattr("pipeline.embed.httpx.post", fake_post)
    texts = ["first chunk", "second chunk"]

    vectors = embed(texts, _settings())

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert requests == [
        (
            "http://ollama.test:11434/api/embed",
            {"model": "nomic-embed-text", "input": texts},
            120.0,
        )
    ]


def test_embed_batches_multiple_chunks(monkeypatch) -> None:
    inputs: list[list[str]] = []

    def fake_post(
        _url: str, *, json: dict[str, object], timeout: float
    ) -> httpx.Response:
        del timeout
        batch = json["input"]
        assert isinstance(batch, list)
        inputs.append(batch)
        return _response([[float(len(inputs)), 1.0] for _ in batch])

    monkeypatch.setattr("pipeline.embed.httpx.post", fake_post)
    texts = [f"chunk {index}" for index in range(70)]

    vectors = embed(texts, _settings())

    assert [len(batch) for batch in inputs] == [32, 32, 6]
    assert [vector[0] for vector in vectors] == [1.0] * 32 + [2.0] * 32 + [3.0] * 6


def test_embed_empty_input_makes_no_request(monkeypatch) -> None:
    def unexpected_request(*_args: object, **_kwargs: object) -> httpx.Response:
        raise AssertionError("empty input should not call Ollama")

    monkeypatch.setattr("pipeline.embed.httpx.post", unexpected_request)

    assert embed([], _settings()) == []


def test_embed_preserves_chunk_text_ids_and_metadata(monkeypatch) -> None:
    chunk = Chunk(
        id="handbook:2026:4:1",
        text="Cited policy text.",
        source="handbook.pdf",
        section="Academic Warning",
        page=4,
        doc_version="2026",
        superseded=False,
    )
    original = chunk.__dict__.copy()
    monkeypatch.setattr(
        "pipeline.embed.httpx.post",
        lambda *_args, **_kwargs: _response([[0.1, 0.2, 0.3]]),
    )

    vectors = embed([chunk.text], _settings())

    assert len(vectors) == 1
    assert len(vectors[0]) == 3
    assert chunk.__dict__ == original


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"embeddings": "not a vector list"},
        {"embeddings": []},
        {"embeddings": [[1.0]]},
        {"embeddings": [[1.0, "bad"]]},
        {"embeddings": [[1.0], [1.0, 2.0]]},
    ],
)
def test_embed_rejects_missing_or_malformed_responses(monkeypatch, payload) -> None:
    monkeypatch.setattr(
        "pipeline.embed.httpx.post",
        lambda *_args, **_kwargs: httpx.Response(200, json=payload),
    )

    with pytest.raises(RuntimeError, match="Ollama returned"):
        embed(["first", "second"], _settings())


def test_embed_rejects_non_finite_vector_values(monkeypatch) -> None:
    response = httpx.Response(
        200,
        content=b'{"embeddings":[[NaN]]}',
        headers={"content-type": "application/json"},
    )
    monkeypatch.setattr("pipeline.embed.httpx.post", lambda *_args, **_kwargs: response)

    with pytest.raises(RuntimeError, match="non-finite"):
        embed(["text"], _settings())


def test_embed_rejects_out_of_range_vector_values(monkeypatch) -> None:
    response = httpx.Response(
        200,
        content=b'{"embeddings":[[' + b"9" * 500 + b"]]}",
        headers={"content-type": "application/json"},
    )
    monkeypatch.setattr("pipeline.embed.httpx.post", lambda *_args, **_kwargs: response)

    with pytest.raises(RuntimeError, match="out-of-range"):
        embed(["text"], _settings())


def test_embed_rejects_invalid_json(monkeypatch) -> None:
    response = httpx.Response(
        200,
        content=b"not json",
        headers={"content-type": "application/json"},
    )
    monkeypatch.setattr("pipeline.embed.httpx.post", lambda *_args, **_kwargs: response)

    with pytest.raises(RuntimeError, match="invalid JSON"):
        embed(["text"], _settings())


def test_embed_rejects_inconsistent_dimensions_across_batches(monkeypatch) -> None:
    responses = iter([_response([[1.0, 2.0]] * 32), _response([[1.0]])])
    monkeypatch.setattr(
        "pipeline.embed.httpx.post", lambda *_args, **_kwargs: next(responses)
    )

    with pytest.raises(RuntimeError, match="inconsistent embedding dimensions"):
        embed(["text"] * 33, _settings())


def test_embed_reports_ollama_api_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "pipeline.embed.httpx.post",
        lambda *_args, **_kwargs: httpx.Response(
            404, text='{"error":"model not found"}'
        ),
    )

    with pytest.raises(RuntimeError, match="HTTP 404.*model not found"):
        embed(["some text"], _settings())


def test_embed_reports_connection_failures(monkeypatch) -> None:
    def fail_request(*_args: object, **_kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("pipeline.embed.httpx.post", fail_request)

    with pytest.raises(RuntimeError, match="Unable to connect to Ollama"):
        embed(["some text"], _settings())
