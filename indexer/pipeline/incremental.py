"""Incremental document update management. Tracks document state and manages chunk replacements."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import Chunk, Document


@dataclass
class DocumentState:
    """Tracks the indexed state of one document: source, version, hashes, and chunk IDs."""

    source: str              # file name
    doc_version: str         # version string
    content_hash: str        # SHA-256 of normalized content
    file_hash: str          # SHA-256 of original file
    chunk_ids: list[str] = field(default_factory=list)
    indexed_at: str = ""    # ISO 8601 timestamp
    archived: bool = False   # True if this version was superseded


@dataclass
class CorpusSnapshot:
    """State of the entire corpus: all indexed documents and their chunks."""

    generation: int
    documents: dict[str, DocumentState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Calculate current generation from document states."""
        if not self.documents:
            self.generation = 0
        else:
            max_gen = max(
                (int(re.search(r"\d+", state.indexed_at.split("T")[0] or "")
                      .group() or 0) if state.indexed_at else 0)
                for state in self.documents.values()
            )
            self.generation = max_gen


def _content_hash(text: str) -> str:
    """SHA-256 of normalized text (whitespace collapsed, case-folded for comparison)."""
    normalized = re.sub(r"\s+", " ", text).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    """SHA-256 of the original file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class IncrementalIndexer:
    """Manages incremental updates: skip unchanged documents, replace changed ones, delete removed ones."""

    def __init__(self, state_dir: Path) -> None:
        """Initialize with a directory for persistent state files."""
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot = self._load_snapshot()

    def _snapshot_path(self) -> Path:
        return self.state_dir / "corpus_state.json"

    def _load_snapshot(self) -> CorpusSnapshot:
        """Load the last known corpus state, or return empty snapshot."""
        path = self._snapshot_path()
        if not path.exists():
            return CorpusSnapshot(generation=0)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("state root must be a JSON object")
            raw_documents = data.get("documents", {})
            if not isinstance(raw_documents, dict):
                raise ValueError("state documents must be a JSON object")
            documents = {}
            for source, doc_data in raw_documents.items():
                if not isinstance(doc_data, dict):
                    raise ValueError(f"state for {source!r} must be a JSON object")
                documents[str(source)] = DocumentState(
                    source=str(source),
                    doc_version=str(doc_data.get("doc_version", "")),
                    content_hash=str(doc_data.get("content_hash", "")),
                    file_hash=str(doc_data.get("file_hash", "")),
                    chunk_ids=list(doc_data.get("chunk_ids", [])),
                    indexed_at=str(doc_data.get("indexed_at", "")),
                    archived=bool(doc_data.get("archived", False)),
                )
            return CorpusSnapshot(
                generation=int(data.get("generation", 0)),
                documents=documents,
            )
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError) as exc:
            raise ValueError(f"invalid incremental state file {path}: {exc}") from exc

    def _save_snapshot(self) -> None:
        """Save the current corpus state to disk."""
        data = {
            "generation": self.snapshot.generation,
            "documents": {
                source: {
                    "doc_version": state.doc_version,
                    "content_hash": state.content_hash,
                    "file_hash": state.file_hash,
                    "chunk_ids": state.chunk_ids,
                    "indexed_at": state.indexed_at,
                    "archived": state.archived,
                }
                for source, state in self.snapshot.documents.items()
            },
        }
        self._snapshot_path().write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def classify_document(self, path: Path, doc_version: str) -> tuple[str, str | None]:
        """
        Classify a document as 'unchanged', 'updated', 'new', or 'deleted'.

        Returns: (classification, reason)
        """
        source = path.name
        file_hash = _file_hash(path) if path.is_file() else ""

        if source not in self.snapshot.documents:
            return ("new", None)

        state = self.snapshot.documents[source]
        if state.doc_version != doc_version:
            return ("updated", f"version changed from {state.doc_version} to {doc_version}")
        if state.file_hash != file_hash:
            return ("updated", "file content changed")

        return ("unchanged", None)

    def classify_deleted(self, removed_sources: Sequence[str]) -> dict[str, Any]:
        """Classify deleted documents and return metadata for cleanup."""
        deleted = {}
        for source in removed_sources:
            if source in self.snapshot.documents:
                state = self.snapshot.documents[source]
                deleted[source] = {
                    "version": state.doc_version,
                    "chunk_ids": state.chunk_ids,
                    "count": len(state.chunk_ids),
                }
        return deleted

    def record_indexed(
        self,
        path: Path,
        doc: Document,
        chunks: list[Chunk],
        indexed_at: str,
    ) -> None:
        """Record that a document was successfully indexed with the given chunks."""
        source = path.name
        text = "\n".join(page.text for page in doc.pages if isinstance(page.text, str))
        content_hash = _content_hash(text)
        file_hash = _file_hash(path) if path.is_file() else ""
        chunk_ids = [chunk.id for chunk in chunks]

        self.snapshot.documents[source] = DocumentState(
            source=source,
            doc_version=doc.doc_version,
            content_hash=content_hash,
            file_hash=file_hash,
            chunk_ids=chunk_ids,
            indexed_at=indexed_at,
            archived=False,
        )
        self._save_snapshot()

    def record_superseded(self, source: str, archive_path: Path) -> None:
        """Mark a document as archived when superseded by a newer version."""
        if source in self.snapshot.documents:
            self.snapshot.documents[source].archived = True
            self._save_snapshot()

    def record_deleted(self, removed_sources: Sequence[str]) -> None:
        """Remove deleted documents from tracking."""
        changed = False
        for source in removed_sources:
            if source in self.snapshot.documents:
                del self.snapshot.documents[source]
                changed = True
        if changed:
            self._save_snapshot()

    def get_indexed_sources(self) -> set[str]:
        """Return all currently indexed document sources."""
        return set(self.snapshot.documents.keys())

    def get_archived_sources(self) -> set[str]:
        """Return all archived (superseded) document sources."""
        return {
            source
            for source, state in self.snapshot.documents.items()
            if state.archived
        }

    def get_state_for_source(self, source: str) -> DocumentState | None:
        """Get the recorded state for a document, or None if not found."""
        return self.snapshot.documents.get(source)


class ArchiveManager:
    """Manages archiving and restoring document versions."""

    def __init__(self, archive_dir: Path) -> None:
        """Initialize with the archive directory."""
        self.archive_dir = archive_dir
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def archive_document(self, path: Path, doc_version: str) -> Path:
        """Move a document to archive with version metadata."""
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        
        archived_name = f"{path.stem}@{doc_version}{path.suffix}"
        archive_path = self.archive_dir / archived_name

        counter = 1
        while archive_path.exists():
            archive_path = self.archive_dir / f"{path.stem}@{doc_version}.{counter}{path.suffix}"
            counter += 1

        expected_hash = _file_hash(path)
        shutil.copy2(path, archive_path)
        if _file_hash(archive_path) != expected_hash:
            archive_path.unlink(missing_ok=True)
            raise OSError(f"archive copy verification failed for {path}")
        return archive_path

    @staticmethod
    def archived_version(archive_path: Path) -> str:
        """Return the version recorded in an archive filename."""
        _, separator, version = archive_path.stem.rpartition("@")
        if not separator or not version:
            raise ValueError(f"archive filename has no version metadata: {archive_path.name}")
        version = re.sub(r"\.\d+$", "", version)
        if not version:
            raise ValueError(f"archive filename has an empty version: {archive_path.name}")
        return version

    def list_archived(self) -> dict[str, list[Path]]:
        """List archived documents grouped by base name."""
        archived_by_name: dict[str, list[Path]] = {}
        if self.archive_dir.exists():
            for path in sorted(self.archive_dir.iterdir()):
                if path.is_file():
                    base = path.stem.split("@")[0]
                    if base not in archived_by_name:
                        archived_by_name[base] = []
                    archived_by_name[base].append(path)
        return archived_by_name

    def restore_document(self, archive_path: Path, restore_to: Path) -> Path:
        """Restore an archived document to its original location."""
        restore_to.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = restore_to.with_name(
            f".{restore_to.name}.{uuid.uuid4().hex}.restore"
        )
        expected_hash = _file_hash(archive_path)
        try:
            shutil.copy2(archive_path, temporary_path)
            if _file_hash(temporary_path) != expected_hash:
                raise OSError(f"restore copy verification failed for {archive_path}")
            os.replace(temporary_path, restore_to)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        return restore_to


class ChangeReport:
    """Machine-readable report of indexing changes: what changed, what was skipped, what was deleted."""

    def __init__(self) -> None:
        self.unchanged_count = 0
        self.updated_count = 0
        self.new_count = 0
        self.deleted_count = 0
        self.deleted_chunk_count = 0
        self.chunks_embedded = 0
        self.chunks_skipped = 0
        self.documents: list[dict[str, Any]] = []

    def record_unchanged(self, source: str, chunk_count: int) -> None:
        self.unchanged_count += 1
        self.chunks_skipped += chunk_count
        self.documents.append({
            "source": source,
            "classification": "unchanged",
            "chunks_skipped": chunk_count,
        })

    def record_updated(self, source: str, old_chunks: int, new_chunks: int) -> None:
        self.updated_count += 1
        self.chunks_embedded += new_chunks
        self.documents.append({
            "source": source,
            "classification": "updated",
            "old_chunks": old_chunks,
            "new_chunks": new_chunks,
        })

    def record_new(self, source: str, chunk_count: int) -> None:
        self.new_count += 1
        self.chunks_embedded += chunk_count
        self.documents.append({
            "source": source,
            "classification": "new",
            "chunks": chunk_count,
        })

    def record_deleted(self, source: str, chunk_count: int) -> None:
        self.deleted_count += 1
        self.deleted_chunk_count += chunk_count
        self.documents.append({
            "source": source,
            "classification": "deleted",
            "chunks_removed": chunk_count,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": {
                "discovered": self.unchanged_count + self.updated_count + self.new_count,
                "unchanged": self.unchanged_count,
                "updated": self.updated_count,
                "new": self.new_count,
                "deleted": self.deleted_count,
                "total_chunks_embedded": self.chunks_embedded,
                "total_chunks_skipped": self.chunks_skipped,
                "total_chunks_removed": self.deleted_chunk_count,
            },
            "documents": self.documents,
        }
