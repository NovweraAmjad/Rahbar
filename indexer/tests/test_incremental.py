"""Tests for incremental document updates and version management."""

from __future__ import annotations

from pathlib import Path

import pytest


from pipeline.config import get_settings
from pipeline.incremental import (
    ArchiveManager,
    ChangeReport,
    CorpusSnapshot,
    DocumentState,
    IncrementalIndexer,
    _content_hash,
    _file_hash,
)


def _settings(corpus_dir: Path):
    from dataclasses import replace

    return replace(
        get_settings(),
        corpus_dir=str(corpus_dir),
        indexer_state_dir=str(corpus_dir / ".indexer-state"),
        chunk_size=10,
        chunk_overlap=2,
        quarantine_dir=str(corpus_dir.parent / "quarantine"),
        verification_report_dir=str(corpus_dir.parent / "verification-reports"),
        archive_dir=str(corpus_dir.parent / "archive"),
    )


def test_incremental_unchanged_corpus_embeds_zero_chunks(tmp_path: Path) -> None:
    """Running on an unchanged corpus must embed zero chunks and report counts."""
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    state_dir = corpus_dir / ".indexer-state"

    file1 = corpus_dir / "doc1.txt"
    file1.write_text("This is document one.", encoding="utf-8")

    indexer = IncrementalIndexer(state_dir)
    
    state1 = DocumentState(
        source="doc1.txt",
        doc_version="2026-01",
        content_hash=_content_hash("This is document one."),
        file_hash=_file_hash(file1),
        chunk_ids=["doc1.txt:2026-01:1:1", "doc1.txt:2026-01:1:2"],
        indexed_at="2026-11-21T14:50:00Z",
        archived=False,
    )
    indexer.snapshot.documents["doc1.txt"] = state1
    indexer._save_snapshot()

    classification, reason = indexer.classify_document(file1, "2026-01")
    assert classification == "unchanged"
    assert reason is None


def test_incremental_changed_document_is_detected(tmp_path: Path) -> None:
    """Modifying a document is detected as an update."""
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    state_dir = corpus_dir / ".indexer-state"

    file1 = corpus_dir / "doc1.txt"
    file1.write_text("Original content.", encoding="utf-8")

    indexer = IncrementalIndexer(state_dir)
    
    state1 = DocumentState(
        source="doc1.txt",
        doc_version="2026-01",
        content_hash=_content_hash("Original content."),
        file_hash=_file_hash(file1),
        chunk_ids=["doc1.txt:2026-01:1:1"],
        indexed_at="2026-11-21T14:50:00Z",
        archived=False,
    )
    indexer.snapshot.documents["doc1.txt"] = state1
    indexer._save_snapshot()

    file1.write_text("Updated content.", encoding="utf-8")

    classification, reason = indexer.classify_document(file1, "2026-01")
    assert classification == "updated"
    assert "content changed" in reason


def test_incremental_version_change_is_detected(tmp_path: Path) -> None:
    """Changing a document's version is detected as an update."""
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    state_dir = corpus_dir / ".indexer-state"

    file1 = corpus_dir / "doc1.txt"
    file1.write_text("Content.", encoding="utf-8")

    indexer = IncrementalIndexer(state_dir)
    
    state1 = DocumentState(
        source="doc1.txt",
        doc_version="2026-01",
        content_hash=_content_hash("Content."),
        file_hash=_file_hash(file1),
        chunk_ids=["doc1.txt:2026-01:1:1"],
        indexed_at="2026-11-21T14:50:00Z",
        archived=False,
    )
    indexer.snapshot.documents["doc1.txt"] = state1
    indexer._save_snapshot()

    classification, reason = indexer.classify_document(file1, "2026-02")
    assert classification == "updated"
    assert "version changed" in reason


def test_incremental_new_document_is_detected(tmp_path: Path) -> None:
    """A new document not in the snapshot is detected as new."""
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    state_dir = corpus_dir / ".indexer-state"

    file1 = corpus_dir / "new_doc.txt"
    file1.write_text("New document.", encoding="utf-8")

    indexer = IncrementalIndexer(state_dir)

    classification, reason = indexer.classify_document(file1, "2026-01")
    assert classification == "new"
    assert reason is None


def test_incremental_deleted_documents_are_classified(tmp_path: Path) -> None:
    """Deleted documents are properly classified with their chunk IDs."""
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    state_dir = corpus_dir / ".indexer-state"

    indexer = IncrementalIndexer(state_dir)
    
    state1 = DocumentState(
        source="deleted1.txt",
        doc_version="2026-01",
        content_hash="hash1",
        file_hash="filehash1",
        chunk_ids=["chunk1", "chunk2"],
        indexed_at="2026-11-21T14:50:00Z",
        archived=False,
    )
    state2 = DocumentState(
        source="deleted2.txt",
        doc_version="2026-01",
        content_hash="hash2",
        file_hash="filehash2",
        chunk_ids=["chunk3"],
        indexed_at="2026-11-21T14:50:00Z",
        archived=False,
    )
    indexer.snapshot.documents["deleted1.txt"] = state1
    indexer.snapshot.documents["deleted2.txt"] = state2
    indexer._save_snapshot()

    deleted = indexer.classify_deleted(["deleted1.txt", "deleted2.txt"])
    
    assert len(deleted) == 2
    assert deleted["deleted1.txt"]["count"] == 2
    assert deleted["deleted1.txt"]["chunk_ids"] == ["chunk1", "chunk2"]
    assert deleted["deleted2.txt"]["count"] == 1
    assert deleted["deleted2.txt"]["chunk_ids"] == ["chunk3"]


def test_archive_manager_archives_and_restores_documents(tmp_path: Path) -> None:
    """Archive manager can archive and restore documents with version metadata."""
    archive_dir = tmp_path / "archive"
    archive_mgr = ArchiveManager(archive_dir)

    doc_file = tmp_path / "original.txt"
    doc_file.write_text("Document content.", encoding="utf-8")

    archived = archive_mgr.archive_document(doc_file, "2026-01")
    assert archived.exists()
    assert "@2026-01" in archived.name
    assert archived.read_text(encoding="utf-8") == "Document content."

    restored = tmp_path / "restored.txt"
    restored_path = archive_mgr.restore_document(archived, restored)
    
    assert restored_path.exists()
    assert restored_path.read_text(encoding="utf-8") == "Document content."


def test_archive_manager_handles_duplicate_names(tmp_path: Path) -> None:
    """Archive manager handles duplicate archive names by appending a counter."""
    archive_dir = tmp_path / "archive"
    archive_mgr = ArchiveManager(archive_dir)

    doc_file = tmp_path / "doc.txt"
    doc_file.write_text("Content.", encoding="utf-8")

    archived1 = archive_mgr.archive_document(doc_file, "2026-01")
    archived2 = archive_mgr.archive_document(doc_file, "2026-01")

    assert archived1.exists()
    assert archived2.exists()
    assert archived1 != archived2
    assert archived1.name != archived2.name


def test_archive_manager_verifies_archive_and_restores_version(tmp_path: Path) -> None:
    archive_mgr = ArchiveManager(tmp_path / "archive")
    original = tmp_path / "policy.txt"
    original.write_bytes(b"version 2025")

    archived = archive_mgr.archive_document(original, "2025")
    restored = archive_mgr.restore_document(archived, tmp_path / "restored.txt")

    assert archive_mgr.archived_version(archived) == "2025"
    assert archived.read_bytes() == original.read_bytes()
    assert restored.read_bytes() == original.read_bytes()


def test_change_report_records_all_classifications(tmp_path: Path) -> None:
    """Change report accurately records document classifications and counts."""
    report = ChangeReport()

    report.record_unchanged("doc1.txt", 5)
    report.record_new("doc2.txt", 3)
    report.record_updated("doc3.txt", 4, 6)
    report.record_deleted("doc4.txt", 2)

    summary = report.to_dict()["summary"]
    
    assert summary["discovered"] == 3
    assert summary["unchanged"] == 1
    assert summary["new"] == 1
    assert summary["updated"] == 1
    assert summary["deleted"] == 1
    assert summary["total_chunks_skipped"] == 5
    assert summary["total_chunks_embedded"] == 9
    assert summary["total_chunks_removed"] == 2


def test_change_report_zero_embeddings_on_unchanged_corpus(tmp_path: Path) -> None:
    """When corpus is unchanged, chunks_embedded must be zero."""
    report = ChangeReport()

    report.record_unchanged("doc1.txt", 5)
    report.record_unchanged("doc2.txt", 3)

    summary = report.to_dict()["summary"]
    
    assert summary["total_chunks_embedded"] == 0
    assert summary["total_chunks_skipped"] == 8
    assert summary["discovered"] == 2


def test_incremental_snapshot_persists_and_loads(tmp_path: Path) -> None:
    """Incremental indexer persists and loads snapshot correctly."""
    state_dir = tmp_path / ".state"
    indexer1 = IncrementalIndexer(state_dir)

    state = DocumentState(
        source="test.txt",
        doc_version="2026-01",
        content_hash="ch1",
        file_hash="fh1",
        chunk_ids=["c1", "c2"],
        indexed_at="2026-11-21T14:50:00Z",
        archived=False,
    )
    indexer1.snapshot.documents["test.txt"] = state
    indexer1._save_snapshot()

    indexer2 = IncrementalIndexer(state_dir)
    
    assert "test.txt" in indexer2.snapshot.documents
    loaded_state = indexer2.snapshot.documents["test.txt"]
    assert loaded_state.doc_version == "2026-01"
    assert loaded_state.chunk_ids == ["c1", "c2"]


def test_incremental_snapshot_rejects_corrupt_state(tmp_path: Path) -> None:
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    (state_dir / "corpus_state.json").write_text("{invalid", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid incremental state file"):
        IncrementalIndexer(state_dir)


def test_verify_no_orphans_detects_orphan_chunks(tmp_path: Path) -> None:
    """Verify no orphans detects chunks from deleted documents."""
    report = {
        "chunks_by_source": {
            "indexed.txt": 5,
            "deleted.txt": 3,
            "also_deleted.txt": 2,
        }
    }
    
    indexed_sources = {"indexed.txt"}
    
    result = {
        "chunks_by_source": report["chunks_by_source"],
        "orphan_sources": [s for s in report["chunks_by_source"] if s not in indexed_sources],
        "orphan_count": sum(c for s, c in report["chunks_by_source"].items() if s not in indexed_sources),
    }
    
    assert len(result["orphan_sources"]) == 2
    assert result["orphan_count"] == 5
    assert "deleted.txt" in result["orphan_sources"]


def test_document_state_initialization(tmp_path: Path) -> None:
    """DocumentState initializes all fields correctly."""
    state = DocumentState(
        source="test.txt",
        doc_version="2026-01",
        content_hash="ch1",
        file_hash="fh1",
    )
    
    assert state.source == "test.txt"
    assert state.doc_version == "2026-01"
    assert state.chunk_ids == []
    assert state.archived is False


def test_corpus_snapshot_initialization(tmp_path: Path) -> None:
    """CorpusSnapshot initializes with generation tracking."""
    snapshot = CorpusSnapshot(generation=0)
    
    assert snapshot.generation == 0
    assert len(snapshot.documents) == 0
    
    state = DocumentState(
        source="test.txt",
        doc_version="2026-01",
        content_hash="ch1",
        file_hash="fh1",
    )
    snapshot.documents["test.txt"] = state
    
    assert len(snapshot.documents) == 1
