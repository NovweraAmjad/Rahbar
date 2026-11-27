"""Tests and evidence for Assignment 03 Batch B."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from pipeline.config import get_settings
from pipeline.extract import extract
from pipeline.incremental import ArchiveManager
from pipeline.run import run_incremental_pipeline, run_verification_only
from pipeline.verify import DocumentVerifier


REPO_ROOT = Path(__file__).resolve().parents[2]
BATCH_B = REPO_ROOT / "corpus" / "batch-b"


def _settings(corpus_dir: Path) -> object:
    return replace(
        get_settings(),
        corpus_dir=str(corpus_dir),
        indexer_state_dir=str(corpus_dir / ".indexer-state"),
        chunk_size=20,
        chunk_overlap=2,
        quarantine_dir=str(corpus_dir.parent / "quarantine"),
        verification_report_dir=str(corpus_dir.parent / "verification-reports"),
        archive_dir=str(corpus_dir.parent / "archive"),
        near_duplicate_threshold=0.95,
    )


def _batch_embedder(text: str) -> list[float]:
    lower = text.casefold()
    if "batch exact duplicate" in lower:
        return [1.0, 0.0, 0.0, 0.0]
    if "batch near duplicate" in lower:
        return [0.0, 1.0, 0.0, 0.0]
    if "batch academic policy" in lower and "2026" in lower:
        return [0.0, 0.0, 0.0, 1.0]
    if "batch academic policy" in lower and "2025" in lower:
        return [0.0, 0.0, 1.0, 0.0]
    digest = __import__("hashlib").sha256(text.encode("utf-8")).digest()
    values = [float(digest[index] + 1) for index in range(4)]
    return values


def _copy_batch(tmp_path: Path) -> Path:
    destination = tmp_path / "batch-b"
    shutil.copytree(BATCH_B, destination)
    return destination


def test_batch_b_manifest_has_all_required_planted_problems() -> None:
    entries = json.loads((BATCH_B / "_index.json").read_text(encoding="utf-8"))

    assert len(entries) >= 10
    issues = {str(entry["intended_issue"]) for entry in entries}
    assert {"exact_duplicate", "near_duplicate", "truncated", "missing_required_metadata",
            "superseded_old_version", "newer_version", "prompt_injection",
            "corrupt"} <= issues

    old = next(entry for entry in entries if entry["intended_issue"] == "superseded_old_version")
    new = next(entry for entry in entries if entry["intended_issue"] == "newer_version")
    assert old["doc_version"] == "2025"
    assert new["doc_version"] == "2026"
    assert old["superseded"] is True
    assert new["superseded"] is False


def test_batch_b_real_verifier_detects_each_planted_problem(tmp_path: Path) -> None:
    corpus = _copy_batch(tmp_path)
    settings = _settings(corpus)
    candidates = json.loads((corpus / "_index.json").read_text(encoding="utf-8"))
    verifier = DocumentVerifier(
        quarantine_dir=Path(settings.quarantine_dir),
        report_dir=Path(settings.verification_report_dir),
        archive_dir=Path(settings.archive_dir),
        near_duplicate_threshold=0.95,
        embedder=_batch_embedder,
        candidates=candidates,
    )

    results = {}
    for entry in candidates:
        path = corpus / str(entry["file"])
        document = extract(
            str(path),
            str(entry["doc_version"]),
            bool(entry["superseded"]),
        )
        document.title = str(entry["name"])
        document.source_url = str(entry["url"])
        document.fetched_at = str(entry["fetched_at"])
        results[document.source] = verifier.verify(path, document)

    def result(name: str) -> dict:
        return results[name]  # type: ignore[no-any-return]

    exact = result("batch-exact-b.txt")
    assert exact["decision"] == "quarantine"
    assert exact["checks"]["uniqueness"]["exact_duplicate"]["status"] == "fail"

    near = result("batch-near-b.txt")
    assert near["decision"] == "quarantine"
    near_duplicate = near["checks"]["uniqueness"]["near_duplicate"]
    assert near_duplicate["status"] == "fail"
    assert near_duplicate["matching_document"] == "batch-near-a.txt"
    assert near_duplicate["similarity"] is not None
    assert float(near_duplicate["similarity"]) >= 0.95

    assert result("batch-truncated.txt")["checks"]["completeness"]["status"] == "fail"
    assert result("batch-missing-title.txt")["checks"]["completeness"]["status"] == "fail"
    assert result("batch-corrupt.txt")["checks"]["quality"]["status"] == "fail"

    old = result("batch-academic-policy-2025.txt")
    assert old["decision"] == "quarantine"
    assert old["checks"]["recency"]["status"] == "fail"
    assert old["checks"]["recency"]["superseded"] is True

    injection = result("batch-injection.txt")
    assert injection["decision"] == "quarantine"
    assert injection["checks"]["quality"]["status"] == "fail"
    assert "prompt-injection" in " ".join(injection["checks"]["quality"]["reasons"])

    assert result("batch-valid-attendance.txt")["decision"] == "index"
    assert result("batch-academic-policy-2026.txt")["decision"] == "index"
    assert result("batch-scholarship.txt")["decision"] == "index"

    report_path = verifier.write_report("batch-b-test")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["total"] == 12
    assert report["summary"]["quarantine"] >= 7


def test_batch_b_verification_only_quarantines_without_indexing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    corpus = _copy_batch(tmp_path)
    settings = _settings(corpus)

    monkeypatch.setattr(
        "pipeline.embed.embed",
        lambda texts, _settings: [_batch_embedder(text) for text in texts],
    )

    accepted = run_verification_only(settings)
    assert accepted == 5

    reports = list(Path(settings.verification_report_dir).glob("verification-*.json"))
    assert reports
    report = json.loads(reports[-1].read_text(encoding="utf-8"))
    quarantined = {
        document["source"]
        for document in report["documents"]
        if document["decision"] == "quarantine"
    }
    assert "batch-exact-b.txt" in quarantined
    assert "batch-near-b.txt" in quarantined
    assert "batch-truncated.txt" in quarantined
    assert "batch-missing-title.txt" in quarantined
    assert "batch-corrupt.txt" in quarantined
    assert "batch-academic-policy-2025.txt" in quarantined
    assert "batch-injection.txt" in quarantined


def test_batch_b_incremental_is_idempotent_and_supersedes_restores(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    corpus = _copy_batch(tmp_path)
    settings = _settings(corpus)
    stored_sources: list[str] = []
    deleted_ids: list[str] = []

    monkeypatch.setattr(
        "pipeline.embed.embed",
        lambda texts, _settings: [_batch_embedder(text) for text in texts],
    )
    monkeypatch.setattr(
        "pipeline.store.store",
        lambda chunks, vectors, _settings, **kwargs: stored_sources.extend(
            chunk.source for chunk in chunks
        ) or len(chunks),
    )
    monkeypatch.setattr(
        "pipeline.cache.invalidate_semantic_cache",
        lambda _settings: 0,
    )
    monkeypatch.setattr(
        "pipeline.chroma_ops.delete_chunks_by_ids",
        lambda _settings, ids: deleted_ids.extend(ids) or len(ids),
    )
    monkeypatch.setattr(
        "pipeline.chroma_ops.mark_superseded",
        lambda _settings, ids: len(ids),
    )

    first = run_incremental_pipeline(settings)
    assert first["summary"]["discovered"] == 5
    assert first["chunks_embedded"] > 0
    assert set(stored_sources).isdisjoint(
        {
            "batch-exact-b.txt",
            "batch-near-b.txt",
            "batch-truncated.txt",
            "batch-missing-title.txt",
            "batch-corrupt.txt",
            "batch-academic-policy-2025.txt",
            "batch-injection.txt",
        }
    )

    second = run_incremental_pipeline(settings)
    assert second["chunks_embedded"] == 0
    assert second["chunks_skipped"] > 0
    assert second["summary"]["unchanged"] == 5

    archive_dir = Path(settings.archive_dir)
    candidates = [
        path
        for path in archive_dir.glob("batch-academic-policy-2025@*.txt")
    ]
    if not candidates:
        archive_mgr = ArchiveManager(archive_dir)
        archive_mgr.archive_document(
            corpus / "batch-academic-policy-2025.txt",
            "2025",
        )
        candidates = list(archive_dir.glob("batch-academic-policy-2025@*.txt"))
    assert candidates

    restored = archive_dir.parent / "restored-batch-academic-policy-2025.txt"
    archive_mgr = ArchiveManager(archive_dir)
    restored_path = archive_mgr.restore_document(candidates[0], restored)
    assert restored_path.read_text(encoding="utf-8").startswith(
        "# Batch Academic Policy"
    )


def test_batch_b_embedding_or_storage_failure_preserves_previous_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    corpus = _copy_batch(tmp_path)
    settings = _settings(corpus)

    monkeypatch.setattr(
        "pipeline.embed.embed",
        lambda texts, _settings: [_batch_embedder(text) for text in texts],
    )
    monkeypatch.setattr(
        "pipeline.store.store",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated storage failure")
        ),
    )
    monkeypatch.setattr(
        "pipeline.cache.invalidate_semantic_cache",
        lambda _settings: 0,
    )
    monkeypatch.setattr(
        "pipeline.chroma_ops.delete_chunks_by_ids",
        lambda _settings, ids: len(ids),
    )

    with pytest.raises(RuntimeError, match="storage stage failed"):
        run_incremental_pipeline(settings)

    state_path = corpus / ".indexer-state" / "corpus_state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["documents"] == {}
