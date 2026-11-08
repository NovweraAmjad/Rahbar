from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from pipeline.config import get_settings
from pipeline.models import Chunk, Document, Page
from pipeline.run import run_pipeline


def _settings(corpus_dir: Path):
    return replace(
        get_settings(),
        corpus_dir=str(corpus_dir),
        chunk_size=10,
        chunk_overlap=2,
    )


def _document(path: str, version: str, superseded: bool) -> Document:
    source = Path(path).name
    return Document(
        source=source,
        doc_version=version,
        superseded=superseded,
        pages=[Page(source=source, page=1, text="Example extracted policy text.")],
    )


def _chunk(source: str, *, version: str = "2026", superseded: bool = False) -> Chunk:
    return Chunk(
        id=f"{source}:{version}:1:1",
        text="Example extracted policy text.",
        source=source,
        section="",
        page=1,
        doc_version=version,
        superseded=superseded,
    )


def _corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "policy.txt").write_text("source text", encoding="utf-8")
    return root


def test_run_pipeline_executes_all_stages_in_order_and_logs(monkeypatch, tmp_path, capsys) -> None:
    root = _corpus(tmp_path)
    (root / "_index.json").write_text(
        json.dumps(
            [{"file": "policy.txt", "doc_version": "2024", "superseded": True}]
        ),
        encoding="utf-8",
    )
    calls: list[str] = []
    document = _document(str(root / "policy.txt"), "2024", True)
    piece = _chunk("policy.txt", version="2024", superseded=True)

    def fake_extract(path: str, version: str, superseded: bool) -> Document:
        calls.append("extract")
        assert (version, superseded) == ("2024", True)
        return _document(path, version, superseded)

    def fake_clean(doc: Document) -> Document:
        calls.append("clean")
        return doc

    def fake_chunk(doc: Document, size: int, overlap: int) -> list[Chunk]:
        calls.append("chunk")
        assert (size, overlap) == (10, 2)
        assert doc.source == document.source
        assert (doc.doc_version, doc.superseded) == ("2024", True)
        return [piece]

    def fake_embed(texts: list[str], settings) -> list[list[float]]:
        calls.append("embed")
        assert texts == [piece.text]
        assert settings.embed_model == "nomic-embed-text"
        return [[0.1, 0.2]]

    def fake_store(chunks, vectors, settings, *, prune_stale: bool = False) -> int:
        calls.append("store")
        assert chunks == [piece]
        assert (chunks[0].source, chunks[0].page) == ("policy.txt", 1)
        assert (chunks[0].doc_version, chunks[0].superseded) == ("2024", True)
        assert vectors == [[0.1, 0.2]]
        assert settings.corpus_dir == str(root)
        assert prune_stale is True
        return 1

    monkeypatch.setattr("pipeline.extract.extract", fake_extract)
    monkeypatch.setattr("pipeline.clean.clean", fake_clean)
    monkeypatch.setattr("pipeline.chunk.chunk", fake_chunk)
    monkeypatch.setattr("pipeline.embed.embed", fake_embed)
    monkeypatch.setattr("pipeline.store.store", fake_store)

    assert run_pipeline(_settings(root)) == 1

    assert calls == ["extract", "clean", "chunk", "embed", "store"]
    output = capsys.readouterr().out
    for event in (
        "documents discovered",
        "extracted",
        "cleaned",
        "chunked",
        "embeddings generated",
        "stored",
    ):
        assert event in output


def test_run_pipeline_rejects_empty_corpus(tmp_path) -> None:
    root = tmp_path / "empty-corpus"
    root.mkdir()

    with pytest.raises(RuntimeError, match="no documents found"):
        run_pipeline(_settings(root))


def test_run_pipeline_reports_missing_corpus_directory(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="corpus directory does not exist"):
        run_pipeline(_settings(tmp_path / "missing"))


def test_run_pipeline_fails_on_extraction_failure_before_embedding_or_storage(
    monkeypatch, tmp_path
) -> None:
    root = _corpus(tmp_path)
    calls: list[str] = []

    def fail_extract(*_args) -> Document:
        calls.append("extract")
        raise OSError("file cannot be read")

    monkeypatch.setattr("pipeline.extract.extract", fail_extract)

    def record_embed(*_args) -> list[list[float]]:
        calls.append("embed")
        return [[0.1]]

    def record_store(*_args, **_kwargs) -> int:
        calls.append("store")
        return 1

    monkeypatch.setattr(
        "pipeline.embed.embed",
        record_embed,
    )
    monkeypatch.setattr(
        "pipeline.store.store",
        record_store,
    )

    with pytest.raises(RuntimeError, match="extract stage failed for policy.txt"):
        run_pipeline(_settings(root))
    assert calls == ["extract"]


def test_run_pipeline_does_not_store_when_extraction_returns_no_pages(
    monkeypatch, tmp_path
) -> None:
    root = _corpus(tmp_path)
    monkeypatch.setattr(
        "pipeline.extract.extract",
        lambda path, version, superseded: Document(Path(path).name, version, superseded),
    )
    monkeypatch.setattr(
        "pipeline.store.store",
        lambda *_args, **_kwargs: pytest.fail("store should not be called"),
    )

    with pytest.raises(RuntimeError, match="extraction produced no pages"):
        run_pipeline(_settings(root))


def test_run_pipeline_does_not_store_when_all_documents_are_empty(
    monkeypatch, tmp_path
) -> None:
    root = _corpus(tmp_path)
    monkeypatch.setattr(
        "pipeline.extract.extract",
        lambda path, version, superseded: Document(
            Path(path).name,
            version,
            superseded,
            pages=[Page(source=Path(path).name, page=1, text="")],
        ),
    )
    monkeypatch.setattr("pipeline.clean.clean", lambda doc: doc)
    monkeypatch.setattr(
        "pipeline.chunk.chunk",
        lambda doc, size, overlap: [],
    )
    monkeypatch.setattr(
        "pipeline.embed.embed",
        lambda *_args: pytest.fail("embedding should not run without chunks"),
    )
    monkeypatch.setattr(
        "pipeline.store.store",
        lambda *_args, **_kwargs: pytest.fail("store should not run without chunks"),
    )

    with pytest.raises(RuntimeError, match="no chunks generated"):
        run_pipeline(_settings(root))


def test_run_pipeline_reports_embedding_failure_without_storing(monkeypatch, tmp_path) -> None:
    root = _corpus(tmp_path)
    monkeypatch.setattr(
        "pipeline.extract.extract",
        lambda path, version, superseded: _document(path, version, superseded),
    )
    monkeypatch.setattr("pipeline.clean.clean", lambda doc: doc)
    monkeypatch.setattr(
        "pipeline.chunk.chunk",
        lambda doc, size, overlap: [_chunk(doc.source)],
    )

    def fail_embed(*_args) -> list[list[float]]:
        raise ConnectionError("Ollama unavailable")

    monkeypatch.setattr("pipeline.embed.embed", fail_embed)
    monkeypatch.setattr(
        "pipeline.store.store",
        lambda *_args, **_kwargs: pytest.fail("store should not be called"),
    )

    with pytest.raises(RuntimeError, match="embedding stage failed: Ollama unavailable"):
        run_pipeline(_settings(root))


def test_run_pipeline_reports_storage_failure(monkeypatch, tmp_path) -> None:
    root = _corpus(tmp_path)
    monkeypatch.setattr(
        "pipeline.extract.extract",
        lambda path, version, superseded: _document(path, version, superseded),
    )
    monkeypatch.setattr("pipeline.clean.clean", lambda doc: doc)
    monkeypatch.setattr(
        "pipeline.chunk.chunk",
        lambda doc, size, overlap: [_chunk(doc.source)],
    )
    monkeypatch.setattr("pipeline.embed.embed", lambda texts, settings: [[0.1]])

    def fail_store(*_args, **_kwargs) -> int:
        raise RuntimeError("Chroma unavailable")

    monkeypatch.setattr("pipeline.store.store", fail_store)

    with pytest.raises(RuntimeError, match="storage stage failed: Chroma unavailable"):
        run_pipeline(_settings(root))


def test_run_pipeline_invalidates_cache_before_storage(monkeypatch, tmp_path) -> None:
    root = _corpus(tmp_path)
    calls: list[str] = []
    settings = replace(_settings(root), redis_url="redis://redis:6379/0")
    monkeypatch.setattr(
        "pipeline.extract.extract",
        lambda path, version, superseded: _document(path, version, superseded),
    )
    monkeypatch.setattr("pipeline.clean.clean", lambda doc: doc)
    monkeypatch.setattr("pipeline.chunk.chunk", lambda doc, size, overlap: [_chunk(doc.source)])
    monkeypatch.setattr("pipeline.embed.embed", lambda texts, settings: [[0.1]])

    def record_invalidate(_settings) -> int:
        calls.append("invalidate")
        return 2

    def record_store(*_args, **_kwargs) -> int:
        calls.append("store")
        return 1

    monkeypatch.setattr(
        "pipeline.cache.invalidate_semantic_cache",
        record_invalidate,
    )
    monkeypatch.setattr(
        "pipeline.store.store",
        record_store,
    )

    assert run_pipeline(settings) == 1
    assert calls == ["invalidate", "store"]


def test_run_pipeline_does_not_store_if_cache_invalidation_fails(
    monkeypatch, tmp_path
) -> None:
    root = _corpus(tmp_path)
    monkeypatch.setattr(
        "pipeline.extract.extract",
        lambda path, version, superseded: _document(path, version, superseded),
    )
    monkeypatch.setattr("pipeline.clean.clean", lambda doc: doc)
    monkeypatch.setattr("pipeline.chunk.chunk", lambda doc, size, overlap: [_chunk(doc.source)])
    monkeypatch.setattr("pipeline.embed.embed", lambda texts, settings: [[0.1]])
    monkeypatch.setattr(
        "pipeline.cache.invalidate_semantic_cache",
        lambda _settings: (_ for _ in ()).throw(RuntimeError("cache unavailable")),
    )
    monkeypatch.setattr(
        "pipeline.store.store",
        lambda *_args, **_kwargs: pytest.fail("index must not be modified"),
    )

    with pytest.raises(RuntimeError, match="cache unavailable"):
        run_pipeline(replace(_settings(root), redis_url="redis://redis:6379/0"))


def test_main_returns_nonzero_and_logs_pipeline_failure(monkeypatch, tmp_path, capsys) -> None:
    from pipeline import run

    monkeypatch.setattr("sys.argv", ["pipeline.run"])
    monkeypatch.setenv("CORPUS_DIR", str(tmp_path / "missing"))

    assert run.main() == 1
    assert "indexer failed" in capsys.readouterr().out
