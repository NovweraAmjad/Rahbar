"""The indexer Job's entry point. It runs once and exits (section 4.2).

    python -m pipeline.run --dummy    # week 1: three fake chunks, no model needed
    python -m pipeline.run            # the real pipeline: extract, clean, chunk, embed, store

Exit code 0 means the index was written; anything else fails the Job, and
Kubernetes retries it up to the Job's backoffLimit.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import IndexerSettings, get_settings
from .models import Chunk, Document


def log(msg: str, **fields: object) -> None:
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                      "logger": "rahbar.indexer", "msg": msg, **fields}), flush=True)


def _front_matter(path: Path) -> dict[str, str]:
    """Read `key: value` lines between leading '---' markers (used by eval/fixtures)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    meta: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta


def _manifest_metadata(corpus_dir: str) -> list[dict[str, object]]:
    root = Path(corpus_dir)
    if not root.exists():
        raise FileNotFoundError(f"corpus directory does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"corpus path is not a directory: {root}")
    entries: list[dict[str, object]] = []
    index_paths = [root / "_index.json"]
    index_paths.extend(
        child / "_index.json"
        for child in sorted(root.iterdir())
        if child.is_dir()
    )
    for index in index_paths:
        if not index.exists():
            continue
        try:
            raw_entries = json.loads(index.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(raw_entries, list):
            entries.extend(
                {**entry, "_index_dir": str(index.parent)}
                for entry in raw_entries
                if isinstance(entry, dict)
            )
    if entries:
        return entries

    markdown_entries: list[dict[str, object]] = []
    for path in sorted(root.glob("*")):
        if path.suffix.lower() not in {".pdf", ".md", ".html", ".txt"}:
            continue
        meta = _front_matter(path) if path.suffix.lower() == ".md" else {}
        markdown_entries.append({
            "file": path.name,
            "name": meta.get("title", ""),
            "doc_version": meta.get("doc_version", "unknown"),
            "superseded": meta.get("superseded", "false").lower() == "true",
            "url": meta.get("source", ""),
            "fetched_at": meta.get("date", meta.get("fetched_at", "")),
        })
    return markdown_entries


def _set_manifest_superseded(corpus_dir: str, updates: dict[str, bool]) -> None:
    root = Path(corpus_dir)
    index_paths = [root / "_index.json"]
    index_paths.extend(
        child / "_index.json"
        for child in sorted(root.iterdir())
        if child.is_dir()
    )
    pending_writes: list[tuple[Path, list[object]]] = []
    matched_sources: set[str] = set()

    for index_path in index_paths:
        if not index_path.is_file():
            continue
        try:
            raw_entries = json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(
                f"could not update manifest {index_path}: {type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(raw_entries, list):
            raise ValueError(f"manifest root must be a list: {index_path}")

        changed = False
        for entry in raw_entries:
            if not isinstance(entry, dict):
                continue
            source = Path(str(entry.get("file", ""))).name
            if source in updates:
                entry["superseded"] = updates[source]
                matched_sources.add(source)
                changed = True
        if changed:
            pending_writes.append((index_path, raw_entries))

    missing_sources = set(updates) - matched_sources
    if missing_sources:
        raise ValueError(
            "manifest entries not found for sources: "
            + ", ".join(sorted(missing_sources))
        )

    for index_path, entries in pending_writes:
        temporary_path = index_path.with_name(
            f".{index_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary_path.write_text(
                json.dumps(entries, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(temporary_path, index_path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise


def discover(corpus_dir: str) -> list[tuple[Path, str, bool]]:
    """List (path, doc_version, superseded) for every document to index.

    corpus/fetch.py writes _index.json next to the files it downloads; use it when
    present. Otherwise read Markdown front matter (the CI fixtures work this way).
    """
    root = Path(corpus_dir)
    if not root.exists():
        raise FileNotFoundError(f"corpus directory does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"corpus path is not a directory: {root}")

    entries: list[dict[str, object]] = []
    index_paths = [root / "_index.json"]
    index_paths.extend(
        child / "_index.json"
        for child in sorted(root.iterdir())
        if child.is_dir()
    )
    for index in index_paths:
        if not index.exists():
            continue
        try:
            raw_entries = json.loads(index.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(raw_entries, list):
            continue
        for entry in raw_entries:
            if isinstance(entry, dict):
                entries.append({**entry, "_index_dir": str(index.parent)})
    if entries:
        return [
            (
                Path(str(entry["_index_dir"])) / str(entry["file"]),
                str(entry.get("doc_version", "unknown")),
                bool(entry.get("superseded", False)),
            )
            for entry in entries
            if isinstance(entry.get("file"), str)
            and (Path(str(entry["_index_dir"])) / str(entry["file"])).is_file()
        ]
    found = []
    for path in sorted(root.glob("*")):
        if path.suffix.lower() not in {".pdf", ".md", ".html", ".txt"}:
            continue
        if path.name.lower().startswith("readme"):
            continue
        meta = _front_matter(path) if path.suffix.lower() == ".md" else {}
        found.append((path, meta.get("doc_version", "unknown"),
                      meta.get("superseded", "false").lower() == "true"))
    return found


def run_pipeline(settings: IndexerSettings) -> int:
    from .chunk import chunk
    from .clean import clean
    from .embed import embed
    from .extract import extract
    from .store import store
    from .cache import invalidate_semantic_cache
    from .verify import DocumentVerifier

    candidates = _manifest_metadata(settings.corpus_dir)
    run_id = uuid.uuid4().hex
    verifier = DocumentVerifier(
        quarantine_dir=Path(settings.quarantine_dir),
        report_dir=Path(settings.verification_report_dir),
        archive_dir=Path(settings.archive_dir),
        near_duplicate_threshold=settings.near_duplicate_threshold,
        embedder=None,
        candidates=candidates,
    )

    documents = discover(settings.corpus_dir)
    if not documents:
        verifier.write_report(run_id)
        raise RuntimeError(f"no documents found in {settings.corpus_dir}")
    log(
        "documents discovered",
        count=len(documents),
        sources=[path.name for path, _version, _superseded in documents],
    )
    verifier.embedder = lambda text: embed([text], settings)[0]

    all_chunks: list[Chunk] = []
    for path, doc_version, superseded in documents:
        stage = "extract"
        try:
            candidate = next(
                (
                    item
                    for item in candidates
                    if str(item.get("file", "")) == path.name
                ),
                {},
            )
            title = str(candidate.get("name", candidate.get("title", "")))
            source_url = str(candidate.get("url", candidate.get("source_url", "")))
            fetched_at = str(candidate.get("fetched_at", candidate.get("date", "")))
            doc = extract(str(path), doc_version, superseded)
            doc.title = title
            doc.source_url = source_url
            doc.fetched_at = fetched_at
            log(
                "extracted",
                source=path.name,
                pages=len(doc.pages),
                text_pages=sum(
                    isinstance(page.text, str) and bool(page.text.strip()) for page in doc.pages
                ),
                ocr_pages=sum(page.ocr for page in doc.pages),
            )
        except Exception as exc:
            doc = Document(
                source=path.name,
                doc_version=doc_version,
                superseded=superseded,
                extraction_error=f"{type(exc).__name__}: {exc}",
            )
            log(
                "document extraction failed",
                source=path.name,
                stage=stage,
                error_type=type(exc).__name__,
                error=str(exc),
            )

        verification = verifier.verify(path, doc)
        if verification["decision"] == "quarantine":
            checks = verification["checks"]
            failed_reasons = {}
            if isinstance(checks, dict):
                failed_reasons = {
                    name: check["reasons"]
                    for name, check in checks.items()
                    if isinstance(check, dict) and check["status"] == "fail"
                }
            log(
                "document quarantined",
                source=path.name,
                reasons=failed_reasons,
                quarantine_path=verification.get("quarantine_path"),
            )
            continue

        try:
            stage = "clean"
            doc = clean(doc)
            log("cleaned", source=path.name, pages=len(doc.pages))

            stage = "chunk"
            pieces = chunk(doc, settings.chunk_size, settings.chunk_overlap)
            log("chunked", source=path.name, chunks=len(pieces))
        except Exception as exc:
            log(
                "document processing failed",
                source=path.name,
                stage=stage,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise RuntimeError(
                f"{stage} stage failed for {path.name}: {exc}"
            ) from exc
        all_chunks.extend(pieces)

    verification_report = verifier.write_report(run_id)
    log(
        "verification report written",
        report=str(verification_report),
        quarantined=sum(
            1
            for document in verifier.documents
            if document["decision"] == "quarantine"
        ),
    )

    if not all_chunks:
        raise RuntimeError("no chunks generated; existing Chroma index was not changed")

    try:
        vectors = embed([item.text for item in all_chunks], settings)
        if len(vectors) != len(all_chunks):
            raise RuntimeError(
                f"embedding count mismatch: {len(vectors)} vectors for "
                f"{len(all_chunks)} chunks"
            )
        log(
            "embeddings generated",
            count=len(vectors),
            dimension=len(vectors[0]),
            model=settings.embed_model,
        )
    except Exception as exc:
        log(
            "embedding failed",
            chunks=len(all_chunks),
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise RuntimeError(f"embedding stage failed: {exc}") from exc

    invalidated = invalidate_semantic_cache(settings)
    log("semantic cache invalidated", entries=invalidated, collection=settings.collection)

    try:
        stored = store(all_chunks, vectors, settings, prune_stale=True)
    except Exception as exc:
        log(
            "storage failed",
            chunks=len(all_chunks),
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise RuntimeError(f"storage stage failed: {exc}") from exc

    log("stored", records=stored, submitted=len(all_chunks))
    return stored


def run_verification_only(settings: IndexerSettings) -> int:
    """Extract and verify a corpus without cleaning, chunking, embedding for indexing,
    or writing to Chroma.
    """
    from .embed import embed
    from .extract import extract
    from .verify import DocumentVerifier

    candidates = _manifest_metadata(settings.corpus_dir)
    documents = discover(settings.corpus_dir)
    run_id = uuid.uuid4().hex
    verifier = DocumentVerifier(
        quarantine_dir=Path(settings.quarantine_dir),
        report_dir=Path(settings.verification_report_dir),
        archive_dir=Path(settings.archive_dir),
        near_duplicate_threshold=settings.near_duplicate_threshold,
        embedder=lambda text: embed([text], settings)[0],
        candidates=candidates,
    )

    accepted = 0
    for path, doc_version, superseded in documents:
        candidate = next(
            (
                item
                for item in candidates
                if str(item.get("file", "")) == path.name
            ),
            {},
        )
        title = str(candidate.get("name", candidate.get("title", "")))
        source_url = str(candidate.get("url", candidate.get("source_url", "")))
        fetched_at = str(candidate.get("fetched_at", candidate.get("date", "")))
        try:
            doc = extract(str(path), doc_version, superseded)
            doc.title = title
            doc.source_url = source_url
            doc.fetched_at = fetched_at
        except Exception as exc:
            doc = Document(
                source=path.name,
                doc_version=doc_version,
                superseded=superseded,
                extraction_error=f"{type(exc).__name__}: {exc}",
            )
        result = verifier.verify(path, doc)
        if result["decision"] == "index":
            accepted += 1

    report_path = verifier.write_report(run_id)
    log(
        "verification complete",
        accepted=accepted,
        quarantined=len(documents) - accepted,
        report=str(report_path),
    )
    return accepted


def run_incremental_pipeline(settings: IndexerSettings) -> dict[str, object]:
    """
    Run the full pipeline with incremental update logic.

    - Skip embedding for unchanged documents
    - Replace only changed documents' chunks
    - Delete removed documents' chunks
    - Report accurate counts

    Returns: {
        "run_id": str,
        "total_stored": int,
        "chunks_embedded": int,
        "chunks_skipped": int,
        "chunks_removed": int,
        "summary": {
            "discovered": int,
            "unchanged": int,
            "updated": int,
            "new": int,
            "deleted": int,
        },
        "change_report_path": str,
    }
    """
    from .chunk import chunk
    from .clean import clean
    from .embed import embed
    from .extract import extract
    from .store import store
    from .cache import invalidate_semantic_cache
    from .verify import DocumentVerifier
    from .incremental import IncrementalIndexer, ChangeReport
    from .chroma_ops import delete_chunks_by_ids, mark_superseded

    run_id = uuid.uuid4().hex
    indexed_at = datetime.now(timezone.utc).isoformat()
    state_dir = Path(settings.indexer_state_dir)
    indexer = IncrementalIndexer(state_dir)
    change_report = ChangeReport()

    candidates = _manifest_metadata(settings.corpus_dir)
    verifier = DocumentVerifier(
        quarantine_dir=Path(settings.quarantine_dir),
        report_dir=Path(settings.verification_report_dir),
        archive_dir=Path(settings.archive_dir),
        near_duplicate_threshold=settings.near_duplicate_threshold,
        embedder=None,
        candidates=candidates,
    )

    documents = discover(settings.corpus_dir)
    if not documents and not indexer.get_indexed_sources():
        verifier.write_report(run_id)
        raise RuntimeError(f"no documents found in {settings.corpus_dir}")

    currently_indexed = set(doc[0].name for doc in documents)
    previously_indexed = indexer.get_indexed_sources()
    removed_sources = [
        source
        for source in previously_indexed - currently_indexed
        if not (
            (state := indexer.get_state_for_source(source))
            and state.archived
        )
    ]

    log(
        "documents discovered",
        count=len(documents),
        sources=[path.name for path, _version, _superseded in documents],
    )

    verifier.embedder = lambda text: embed([text], settings)[0]

    all_chunks: list[Chunk] = []
    chunks_to_delete: list[str] = []
    documents_to_record: list[tuple[Path, Document, list[Chunk]]] = []
    removed_chunk_ids: list[str] = []
    quarantined_sources_to_remove: list[str] = []

    for path, doc_version, superseded in documents:
        source = path.name
        classification, reason = indexer.classify_document(path, doc_version)

        stage = "extract"
        try:
            candidate = next(
                (
                    item
                    for item in candidates
                    if str(item.get("file", "")) == path.name
                ),
                {},
            )
            title = str(candidate.get("name", candidate.get("title", "")))
            source_url = str(candidate.get("url", candidate.get("source_url", "")))
            fetched_at = str(candidate.get("fetched_at", candidate.get("date", "")))
            doc = extract(str(path), doc_version, superseded)
            doc.title = title
            doc.source_url = source_url
            doc.fetched_at = fetched_at
            log(
                "extracted",
                source=path.name,
                pages=len(doc.pages),
                text_pages=sum(
                    isinstance(page.text, str) and bool(page.text.strip()) for page in doc.pages
                ),
                ocr_pages=sum(page.ocr for page in doc.pages),
            )
        except Exception as exc:
            doc = Document(
                source=path.name,
                doc_version=doc_version,
                superseded=superseded,
                extraction_error=f"{type(exc).__name__}: {exc}",
            )
            log(
                "document extraction failed",
                source=path.name,
                stage=stage,
                error_type=type(exc).__name__,
                error=str(exc),
            )

        verification = verifier.verify(path, doc)
        if verification["decision"] == "quarantine":
            checks = verification["checks"]
            failed_reasons = {}
            if isinstance(checks, dict):
                failed_reasons = {
                    name: check["reasons"]
                    for name, check in checks.items()
                    if isinstance(check, dict) and check["status"] == "fail"
                }
            recency = checks.get("recency") if isinstance(checks, dict) else None
            if isinstance(recency, dict) and bool(recency.get("superseded")):
                try:
                    archive_value = verification.get("archive_path")
                    if not isinstance(archive_value, str):
                        raise RuntimeError(
                            f"verification did not preserve the superseded document "
                            f"{source!r} in the archive"
                        )
                    archive_path = Path(archive_value)
                    if not archive_path.is_file():
                        raise FileNotFoundError(
                            f"archived document is missing: {archive_path}"
                        )
                    previous_state = indexer.get_state_for_source(source)
                    if previous_state and previous_state.chunk_ids:
                        marked = mark_superseded(settings, previous_state.chunk_ids)
                        log("superseded chunks marked", source=source, chunks=marked)
                    indexer.record_superseded(source, archive_path)
                    log(
                        "superseded document archived",
                        source=source,
                        archive=str(archive_path),
                    )
                except Exception as exc:
                    log(
                        "superseded document archival failed",
                        source=source,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    raise RuntimeError(
                        f"failed to archive superseded document {source}: {exc}"
                    ) from exc
            else:
                previous_state = indexer.get_state_for_source(source)
                if previous_state and not previous_state.archived:
                    removed_chunk_ids.extend(previous_state.chunk_ids)
                    quarantined_sources_to_remove.append(source)
                    change_report.record_deleted(
                        source,
                        len(previous_state.chunk_ids),
                    )
            log(
                "document quarantined",
                source=path.name,
                reasons=failed_reasons,
                quarantine_path=verification.get("quarantine_path"),
            )
            continue

        if classification == "unchanged":
            prev_state = indexer.get_state_for_source(source)
            prev_chunk_count = len(prev_state.chunk_ids) if prev_state else 0
            change_report.record_unchanged(source, prev_chunk_count)
            log("document unchanged", source=source, chunks_skipped=prev_chunk_count)
            continue

        try:
            stage = "clean"
            doc = clean(doc)
            log("cleaned", source=path.name, pages=len(doc.pages))

            stage = "chunk"
            pieces = chunk(doc, settings.chunk_size, settings.chunk_overlap)
            log("chunked", source=path.name, chunks=len(pieces))
        except Exception as exc:
            log(
                "document processing failed",
                source=path.name,
                stage=stage,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise RuntimeError(
                f"{stage} stage failed for {path.name}: {exc}"
            ) from exc

        if classification == "updated":
            prev_state = indexer.get_state_for_source(source)
            prev_chunk_count = len(prev_state.chunk_ids) if prev_state else 0
            chunks_to_delete.extend(prev_state.chunk_ids if prev_state else [])
            change_report.record_updated(source, prev_chunk_count, len(pieces))
            log(
                "document updated",
                source=source,
                old_chunks=prev_chunk_count,
                new_chunks=len(pieces),
            )
        elif classification == "new":
            change_report.record_new(source, len(pieces))
            log("document new", source=source, chunks=len(pieces))

        all_chunks.extend(pieces)
        documents_to_record.append((path, doc, pieces))

    verifier_report = verifier.write_report(run_id)
    log(
        "verification report written",
        report=str(verifier_report),
        quarantined=sum(
            1
            for document in verifier.documents
            if document["decision"] == "quarantine"
        ),
    )

    for source in removed_sources:
        prev_state = indexer.get_state_for_source(source)
        if prev_state:
            change_report.record_deleted(source, len(prev_state.chunk_ids))
            removed_chunk_ids.extend(prev_state.chunk_ids)
            log("document deleted", source=source, chunks_removed=len(prev_state.chunk_ids))

    if not all_chunks:
        if removed_chunk_ids:
            try:
                deleted = delete_chunks_by_ids(settings, removed_chunk_ids)
                log(
                    "deleted chunks",
                    count=deleted,
                    from_sources=removed_sources,
                )
            except Exception as exc:
                log(
                    "deletion failed",
                    chunks=len(removed_chunk_ids),
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                raise RuntimeError(
                    f"failed to delete deleted-document chunks: {exc}"
                ) from exc
        indexer.record_deleted(
            list(dict.fromkeys(removed_sources + quarantined_sources_to_remove))
        )

        log(
            "no new chunks to embed",
            unchanged=change_report.unchanged_count,
            chunks_skipped=change_report.chunks_skipped,
        )
        change_report_path = state_dir / f"change-{run_id}.json"
        change_report_path.parent.mkdir(parents=True, exist_ok=True)
        change_report_path.write_text(
            json.dumps(change_report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return {
            "run_id": run_id,
            "total_stored": 0,
            "chunks_embedded": 0,
            "chunks_skipped": change_report.chunks_skipped,
            "chunks_removed": change_report.deleted_chunk_count,
            "summary": change_report.to_dict()["summary"],
            "change_report_path": str(change_report_path),
        }

    try:
        vectors = embed([item.text for item in all_chunks], settings)
        if len(vectors) != len(all_chunks):
            raise RuntimeError(
                f"embedding count mismatch: {len(vectors)} vectors for "
                f"{len(all_chunks)} chunks"
            )
        log(
            "embeddings generated",
            count=len(vectors),
            dimension=len(vectors[0]),
            model=settings.embed_model,
        )
    except Exception as exc:
        log(
            "embedding failed",
            chunks=len(all_chunks),
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise RuntimeError(f"embedding stage failed: {exc}") from exc

    invalidated = invalidate_semantic_cache(settings)
    log("semantic cache invalidated", entries=invalidated, collection=settings.collection)

    try:
        stored = store(all_chunks, vectors, settings, prune_stale=False)
    except Exception as exc:
        log(
            "storage failed",
            chunks=len(all_chunks),
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise RuntimeError(f"storage stage failed: {exc}") from exc

    log("stored", records=stored, submitted=len(all_chunks))

    active_chunk_ids = {chunk.id for chunk in all_chunks}
    stale_chunk_ids = [
        chunk_id
        for chunk_id in dict.fromkeys(removed_chunk_ids + chunks_to_delete)
        if chunk_id not in active_chunk_ids
    ]
    if stale_chunk_ids:
        try:
            deleted = delete_chunks_by_ids(settings, stale_chunk_ids)
            log("deleted chunks", count=deleted, from_sources=removed_sources)
        except Exception as exc:
            log(
                "deletion failed",
                chunks=len(stale_chunk_ids),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise RuntimeError(f"failed to delete chunks: {exc}") from exc

    indexer.record_deleted(
        list(dict.fromkeys(removed_sources + quarantined_sources_to_remove))
    )

    for indexed_path, indexed_doc, indexed_pieces in documents_to_record:
        indexer.record_indexed(
            indexed_path,
            indexed_doc,
            indexed_pieces,
            indexed_at,
        )

    change_report_path = state_dir / f"change-{run_id}.json"
    change_report_path.parent.mkdir(parents=True, exist_ok=True)
    change_report_path.write_text(
        json.dumps(change_report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return {
        "run_id": run_id,
        "total_stored": stored,
        "chunks_embedded": change_report.chunks_embedded,
        "chunks_skipped": change_report.chunks_skipped,
        "chunks_removed": change_report.deleted_chunk_count,
        "summary": change_report.to_dict()["summary"],
        "change_report_path": str(change_report_path),
    }


def restore_version(
    settings: IndexerSettings, source: str, archived_path: str
) -> dict[str, object]:
    """
    Restore an archived version of a document.

    This reactivates an archived version:
    1. Mark the current version's chunks as archived (superseded) in Chroma
    2. Restore the archived version to the corpus
    3. Re-index the restored version
    4. Update the incremental state

    Returns: {
        "run_id": str,
        "restored": str,
        "previous_version": str,
        "total_stored": int,
    }
    """
    from .chunk import chunk
    from .clean import clean
    from .embed import embed
    from .extract import extract
    from .store import store
    from .cache import invalidate_semantic_cache
    from .incremental import IncrementalIndexer, ArchiveManager
    from .chroma_ops import mark_superseded

    run_id = uuid.uuid4().hex
    indexed_at = datetime.now(timezone.utc).isoformat()
    indexer = IncrementalIndexer(Path(settings.indexer_state_dir))
    archive_mgr = ArchiveManager(Path(settings.archive_dir))

    archive_path = Path(archived_path)
    if not archive_path.exists():
        raise FileNotFoundError(f"archived document not found: {archive_path}")

    current_state = indexer.get_state_for_source(source)
    previous_version = current_state.doc_version if current_state else "unknown"
    restored_version = archive_mgr.archived_version(archive_path)
    candidates = _manifest_metadata(settings.corpus_dir)
    candidate = next(
        (
            item
            for item in candidates
            if Path(str(item.get("file", ""))).name == source
        ),
        None,
    )
    if candidate is None:
        raise ValueError(f"manifest entry not found for restored source {source!r}")
    title = str(candidate.get("name", candidate.get("title", "")))
    source_url = str(candidate.get("url", candidate.get("source_url", "")))
    fetched_at = str(candidate.get("fetched_at", candidate.get("date", "")))
    restore_target = Path(str(candidate.get("_index_dir", settings.corpus_dir))) / str(
        candidate.get("file", source)
    )
    from .verify import _family_match

    manifest_updates = {source: False}
    for other in candidates:
        other_source = Path(str(other.get("file", ""))).name
        if other_source == source:
            continue
        other_state = indexer.get_state_for_source(other_source)
        if (
            other_state is None
            or other_state.archived
            or not _family_match(
                title,
                source,
                str(other.get("name", other.get("title", ""))),
                other_source,
            )
        ):
            continue
        other_path = Path(str(other.get("_index_dir", settings.corpus_dir))) / str(
            other.get("file", other_source)
        )
        if not other_path.is_file():
            raise FileNotFoundError(
                f"active version source is missing: {other_path}"
            )
        other_archive = archive_mgr.archive_document(
            other_path,
            other_state.doc_version,
        )
        mark_superseded(settings, other_state.chunk_ids)
        indexer.record_superseded(other_source, other_archive)
        manifest_updates[other_source] = True
        log(
            "current version archived for restore",
            source=other_source,
            archive=str(other_archive),
        )

    if restore_target.is_file():
        current_version = (
            current_state.doc_version
            if current_state is not None
            else "unknown"
        )
        if current_version != restored_version:
            archive_mgr.archive_document(restore_target, current_version)
            if current_state and not current_state.archived:
                mark_superseded(settings, current_state.chunk_ids)
                log(
                    "marked previous version superseded",
                    source=source,
                    chunks=len(current_state.chunk_ids),
                )

    _set_manifest_superseded(settings.corpus_dir, manifest_updates)
    restored_path = archive_mgr.restore_document(archive_path, restore_target)
    log("restored document", source=source, from_archive=str(archive_path), to=str(restored_path))

    doc = extract(str(restored_path), restored_version, False)
    doc.title = title
    doc.source_url = source_url
    doc.fetched_at = fetched_at

    doc = clean(doc)
    pieces = chunk(doc, settings.chunk_size, settings.chunk_overlap)

    vectors = embed([item.text for item in pieces], settings)
    if len(vectors) != len(pieces):
        raise RuntimeError(f"embedding count mismatch: {len(vectors)} vs {len(pieces)}")

    invalidate_semantic_cache(settings)
    stored = store(pieces, vectors, settings, prune_stale=False)

    indexer.record_indexed(restored_path, doc, pieces, indexed_at)
    log("restored and re-indexed", source=source, chunks=len(pieces), stored=stored)

    return {
        "run_id": run_id,
        "restored": source,
        "previous_version": previous_version,
        "restored_version": doc.doc_version,
        "total_stored": stored,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the RAHBAR index, then exit.")
    parser.add_argument("--dummy", action="store_true", help="write three fake chunks")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="extract and verify documents without cleaning, chunking, embedding, or storing",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="run incremental pipeline: skip embedding for unchanged documents, replace changed ones, delete removed ones",
    )
    parser.add_argument("--corpus-dir", help="override CORPUS_DIR")
    parser.add_argument("--quarantine-dir", help="override QUARANTINE_DIR")
    parser.add_argument("--report-dir", help="override VERIFICATION_REPORT_DIR")
    parser.add_argument("--archive-dir", help="override ARCHIVE_DIR")
    parser.add_argument(
        "--near-duplicate-threshold",
        type=float,
        help="override NEAR_DUPLICATE_THRESHOLD",
    )
    args = parser.parse_args()
    from dataclasses import replace

    settings = get_settings()
    overrides: dict[str, object] = {}
    if args.corpus_dir:
        overrides["corpus_dir"] = args.corpus_dir
    if args.quarantine_dir:
        overrides["quarantine_dir"] = args.quarantine_dir
    if args.report_dir:
        overrides["verification_report_dir"] = args.report_dir
    if args.archive_dir:
        overrides["archive_dir"] = args.archive_dir
    if args.near_duplicate_threshold is not None:
        overrides["near_duplicate_threshold"] = args.near_duplicate_threshold
    if overrides:
        settings = replace(
            settings,
            corpus_dir=str(overrides["corpus_dir"]) if "corpus_dir" in overrides else settings.corpus_dir,
            quarantine_dir=str(overrides["quarantine_dir"]) if "quarantine_dir" in overrides else settings.quarantine_dir,
            verification_report_dir=str(overrides["verification_report_dir"]) if "verification_report_dir" in overrides else settings.verification_report_dir,
            archive_dir=str(overrides["archive_dir"]) if "archive_dir" in overrides else settings.archive_dir,
            near_duplicate_threshold=float(str(overrides["near_duplicate_threshold"])) if "near_duplicate_threshold" in overrides else settings.near_duplicate_threshold,
        )
    log("indexer starting", dummy=args.dummy, incremental=args.incremental, embed_model=settings.embed_model,
        collection=settings.collection, chroma=f"{settings.chroma_host}:{settings.chroma_port}",
        pod=os.environ.get("HOSTNAME", "-"))
    started = time.perf_counter()
    try:
        if args.verify_only:
            count = run_verification_only(settings)
        elif args.incremental:
            result = run_incremental_pipeline(settings)
            count = result["total_stored"]
            log(
                "incremental pipeline complete",
                **result,
                seconds=round(time.perf_counter() - started, 1),
            )
        elif args.dummy:
            from .cache import invalidate_semantic_cache
            from .dummy import write_dummy_index
            from .store import get_client

            invalidated = invalidate_semantic_cache(settings)
            log("semantic cache invalidated", entries=invalidated, collection=settings.collection)
            count = write_dummy_index(settings, get_client(settings))
        else:
            count = run_pipeline(settings)
    except NotImplementedError as exc:
        log("indexer stage not implemented yet", error=str(exc))
        return 2
    except Exception as exc:
        log("indexer failed", error_type=type(exc).__name__, error=str(exc))
        return 1
    log("indexer finished", chunks=count, seconds=round(time.perf_counter() - started, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
