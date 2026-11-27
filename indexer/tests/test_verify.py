from __future__ import annotations

import errno
import json
from dataclasses import replace
from pathlib import Path

from pipeline.config import get_settings
from pipeline.models import Chunk, Document, Page
from pipeline.run import run_pipeline
from pipeline.verify import DocumentVerifier, _quality, _completeness, _recency


def _document(
    source: str,
    text: str,
    *,
    title: str = "Example Policy",
    version: str = "2026",
    superseded: bool = False,
) -> Document:
    return Document(
        source=source,
        doc_version=version,
        superseded=superseded,
        title=title,
        page_count=1,
        pages=[Page(source=source, page=1, text=text)],
    )


def test_quality_detects_empty_garbled_and_extraction_failure() -> None:
    assert _quality(_document("empty.txt", " "))["status"] == "fail"
    assert _quality(_document("garbled.txt", "\ufffd" * 20))["status"] == "fail"
    failed = _document("failed.txt", "")
    failed.extraction_error = "PDF parser raised PDFException"
    assert "extraction failed" in _quality(failed)["reasons"][0]


def test_completeness_requires_metadata_and_detects_truncation_marker(tmp_path: Path) -> None:
    path = tmp_path / "missing.txt"
    path.write_text("ok", encoding="utf-8")
    missing = _document("missing.txt", "A valid short document.", title="", version="unknown")
    result = _completeness(path, missing)
    assert result["status"] == "fail"
    assert "title" in result["reasons"][0]
    assert "date_or_version" in result["reasons"][0]

    truncated = _document("truncated.txt", "This text was [truncated]")
    result = _completeness(path, truncated)
    assert result["status"] == "fail"
    assert "truncation" in " ".join(result["reasons"]).lower()


def test_recency_identifies_superseded_and_newer_versions() -> None:
    candidates = [
        {"file": "old.pdf", "name": "Academic Rules 2023", "doc_version": "2023"},
        {"file": "new.pdf", "name": "Academic Rules 2026", "doc_version": "2026"},
    ]
    old = _document(
        "old.pdf",
        "Academic rules.",
        title="Academic Rules 2023",
        version="2023",
        superseded=True,
    )
    result = _recency(old, candidates)
    assert result["status"] == "fail"
    assert result["superseded"] is True
    assert "new.pdf" in result["reasons"][0]


def test_superseded_document_cannot_poison_current_version_duplicate_check(
    tmp_path: Path,
) -> None:
    old_path = tmp_path / "academic-rules-2025.txt"
    new_path = tmp_path / "academic-rules-2026.txt"
    old_path.write_text("Old rules wording.", encoding="utf-8")
    new_path.write_text("Current rules wording.", encoding="utf-8")
    candidates = [
        {
            "file": old_path.name,
            "name": "Academic Rules 2025",
            "doc_version": "2025",
        },
        {
            "file": new_path.name,
            "name": "Academic Rules 2026",
            "doc_version": "2026",
        },
    ]
    verifier = DocumentVerifier(
        quarantine_dir=tmp_path / "quarantine",
        report_dir=tmp_path / "reports",
        archive_dir=tmp_path / "archive",
        near_duplicate_threshold=0.95,
        embedder=lambda _text: [1.0, 0.0],
        candidates=candidates,
    )

    old = verifier.verify(
        old_path,
        _document(
            old_path.name,
            "Old rules wording.",
            title="Academic Rules 2025",
            version="2025",
            superseded=True,
        ),
    )
    current = verifier.verify(
        new_path,
        _document(
            new_path.name,
            "Current rules wording.",
            title="Academic Rules 2026",
            version="2026",
        ),
    )

    assert old["decision"] == "quarantine"
    assert current["decision"] == "index"
    assert current["checks"]["uniqueness"]["near_duplicate"]["matching_document"] is None


def test_recency_does_not_conflate_related_but_distinct_publications() -> None:
    prospectus = _document(
        "prospectus-2026.pdf",
        "Admissions rules and program information.",
        title="Prospectus 2026 - Academic Rules and Regulations",
        version="2026",
    )
    candidates = [
        {
            "file": "academic-rules-handbook-2026.pdf",
            "name": "Academic Rules Handbook Jun 2026",
            "doc_version": "2026-06",
        }
    ]

    result = _recency(prospectus, candidates)

    assert result["status"] == "pass"
    assert result["superseded"] is False


def test_exact_duplicate_and_near_duplicate_are_reported_and_quarantined(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    reports = tmp_path / "reports"
    archive = tmp_path / "archive"
    source_a = tmp_path / "a.txt"
    source_b = tmp_path / "b.txt"
    source_a.write_text("identical policy text", encoding="utf-8")
    source_b.write_text("identical policy text", encoding="utf-8")

    verifier = DocumentVerifier(
        quarantine_dir=quarantine,
        report_dir=reports,
        archive_dir=archive,
        near_duplicate_threshold=0.90,
        embedder=lambda _text: [1.0, 0.0],
        candidates=[],
    )
    first = verifier.verify(source_a, _document("a.txt", "identical policy text"))
    second = verifier.verify(source_b, _document("b.txt", "identical policy text"))

    uniqueness = second["checks"]["uniqueness"]
    assert first["decision"] == "index"
    assert second["decision"] == "quarantine"
    assert uniqueness["exact_duplicate"]["status"] == "fail"
    assert uniqueness["exact_duplicate"]["matching_document"] == "a.txt"
    assert uniqueness["near_duplicate"]["status"] == "fail"
    assert uniqueness["near_duplicate"]["similarity"] == 1.0
    assert (quarantine / "b.txt").exists()

    report_path = verifier.write_report("test-run")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["total"] == 2
    assert report["summary"]["index"] == 1
    assert report["summary"]["quarantine"] == 1


def test_verifier_never_accepts_extraction_failure(tmp_path: Path) -> None:
    source = tmp_path / "failed.pdf"
    source.write_bytes(b"not a PDF")
    failed = _document("failed.pdf", "")
    failed.extraction_error = "Unable to extract PDF"
    verifier = DocumentVerifier(
        quarantine_dir=tmp_path / "quarantine",
        report_dir=tmp_path / "reports",
        archive_dir=tmp_path / "archive",
        near_duplicate_threshold=0.95,
        embedder=lambda _text: [1.0],
        candidates=[],
    )
    result = verifier.verify(source, failed)
    assert result["checks"]["quality"]["status"] == "fail"
    assert result["decision"] == "quarantine"


def test_quarantine_moves_only_after_verifying_recoverable_copy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "bad.txt"
    source.write_bytes(b"bad document")
    verifier = DocumentVerifier(
        quarantine_dir=tmp_path / "quarantine",
        report_dir=tmp_path / "reports",
        archive_dir=tmp_path / "archive",
        near_duplicate_threshold=0.95,
        embedder=lambda _text: [1.0, 0.0],
        candidates=[],
    )

    result = verifier.verify(source, _document("bad.txt", ""))

    quarantine_path = Path(result["quarantine_path"])
    assert result["quarantine_action"] == "moved_after_verified_copy"
    assert not source.exists()
    assert quarantine_path.read_bytes() == b"bad document"


def test_quarantine_retains_source_on_read_only_mount_and_deduplicates_copy(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "bad.txt"
    source.write_bytes(b"bad document")
    quarantine = tmp_path / "quarantine"
    original_unlink = Path.unlink

    def unlink_with_read_only_source(path: Path, *args, **kwargs) -> None:
        if path == source:
            raise PermissionError(errno.EROFS, "read-only filesystem", str(path))
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink_with_read_only_source)

    def make_verifier() -> DocumentVerifier:
        return DocumentVerifier(
            quarantine_dir=quarantine,
            report_dir=tmp_path / "reports",
            archive_dir=tmp_path / "archive",
            near_duplicate_threshold=0.95,
            embedder=lambda _text: [1.0, 0.0],
            candidates=[],
        )

    first = make_verifier().verify(source, _document("bad.txt", ""))
    second = make_verifier().verify(source, _document("bad.txt", ""))

    assert first["quarantine_action"] == "verified_copy_source_retained"
    assert second["quarantine_path"] == first["quarantine_path"]
    assert source.read_bytes() == b"bad document"
    assert len(list(quarantine.iterdir())) == 1


def test_normal_pipeline_skips_duplicate_before_chunk_and_store(
    monkeypatch, tmp_path: Path
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "one.txt").write_text("Duplicate body.", encoding="utf-8")
    (corpus / "two.txt").write_text("Duplicate body.", encoding="utf-8")
    (corpus / "_index.json").write_text(
        json.dumps([
            {"name": "One", "file": "one.txt", "doc_version": "2026", "superseded": False},
            {"name": "Two", "file": "two.txt", "doc_version": "2026", "superseded": False},
        ]),
        encoding="utf-8",
    )

    def fake_extract(path: str, version: str, superseded: bool) -> Document:
        source = Path(path).name
        title = "One" if source == "one.txt" else "Two"
        return _document(source, "Duplicate body.", title=title, version=version)

    chunked: list[str] = []
    stored: list[str] = []
    monkeypatch.setattr("pipeline.extract.extract", fake_extract)
    monkeypatch.setattr("pipeline.clean.clean", lambda doc: doc)

    def fake_chunk(doc: Document, size: int, overlap: int) -> list[Chunk]:
        chunked.append(doc.source)
        return [Chunk(
            id=f"{doc.source}:2026:1:1",
            text="Duplicate body.",
            source=doc.source,
            section="",
            page=1,
            doc_version="2026",
            superseded=False,
        )]

    monkeypatch.setattr("pipeline.chunk.chunk", fake_chunk)
    monkeypatch.setattr(
        "pipeline.embed.embed",
        lambda texts, settings: [[1.0, 0.0] for _text in texts],
    )

    def fake_store(chunks: list[Chunk], vectors: list[list[float]], settings, **kwargs):
        stored.extend(chunk.source for chunk in chunks)
        return len(stored)

    monkeypatch.setattr("pipeline.store.store", fake_store)

    settings = replace(
        get_settings(),
        corpus_dir=str(corpus),
        indexer_state_dir=str(corpus / ".indexer-state"),
        quarantine_dir=str(tmp_path / "quarantine"),
        verification_report_dir=str(tmp_path / "reports"),
        archive_dir=str(tmp_path / "archive"),
        near_duplicate_threshold=0.95,
        chunk_size=10,
        chunk_overlap=2,
    )
    assert run_pipeline(settings) == 1
    assert chunked == ["one.txt"]
    assert stored == ["one.txt"]
