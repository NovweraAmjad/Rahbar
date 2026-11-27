"""Document verification stage, run after extraction and before cleaning/chunking."""

from __future__ import annotations

import errno
import hashlib
import json
import math
import re
import shutil
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .models import Document


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _text_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _family_title(title: str) -> str:
    text = re.sub(r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b", " ", title, flags=re.I)
    text = re.sub(r"\b\d{4}(?:[-/]\d{2,4})?\b", " ", text)
    words = re.findall(r"[a-z0-9]+", text.casefold())
    stop = {"the", "a", "and", "of", "for", "policy"}
    return " ".join(word for word in words if word not in stop)


def _family_match(left_title: str, left_source: str, right_title: str, right_source: str) -> bool:
    left_title_family = _family_title(left_title)
    right_title_family = _family_title(right_title)
    if left_title_family and left_title_family == right_title_family:
        return True

    left_source_family = _family_title(Path(left_source).stem)
    right_source_family = _family_title(Path(right_source).stem)
    return bool(left_source_family and left_source_family == right_source_family)


def _version_key(value: str) -> tuple[int, ...]:
    numbers = tuple(int(part) for part in re.findall(r"\d+", value))
    return numbers or (-1,)


def _quality(document: Document) -> dict[str, Any]:
    if document.extraction_error:
        return {"status": "fail", "reasons": [f"extraction failed: {document.extraction_error}"]}

    text = "\n".join(page.text for page in document.pages if isinstance(page.text, str))
    stripped = text.strip()
    if not stripped:
        return {"status": "fail", "reasons": ["extracted text is empty"]}

    injection_markers = (
        "ignore all previous instructions",
        "ignore previous instructions",
        "disregard all previous instructions",
        "reveal the hidden system prompt",
        "print the system prompt",
    )
    lower = text.casefold()
    matched_markers = [marker for marker in injection_markers if marker in lower]
    if matched_markers:
        return {
            "status": "fail",
            "reasons": [
                "prompt-injection instruction detected: "
                + ", ".join(matched_markers)
            ],
        }

    control_count = sum(ord(char) < 32 and char not in "\n\r\t" for char in text)
    replacement_count = text.count("\ufffd")
    printable_ratio = sum(char.isprintable() or char in "\n\r\t" for char in text) / len(text)
    alphanumeric = sum(char.isalnum() for char in text)
    alphanumeric_ratio = alphanumeric / len(text)
    if control_count or replacement_count:
        return {
            "status": "fail",
            "reasons": [
                f"garbled text detected: {control_count} control characters, "
                f"{replacement_count} replacement characters"
            ],
        }
    if printable_ratio < 0.85:
        return {
            "status": "fail",
            "reasons": [f"unusable text: only {printable_ratio:.1%} is printable"],
        }
    if len(stripped) >= 20 and alphanumeric_ratio < 0.30:
        return {
            "status": "fail",
            "reasons": [f"garbled text: only {alphanumeric_ratio:.1%} is alphanumeric"],
        }
    return {"status": "pass", "reasons": ["extracted text is non-empty and readable"]}


def _completeness(path: Path, document: Document) -> dict[str, Any]:
    reasons: list[str] = []
    missing: list[str] = []
    if not document.source.strip():
        missing.append("source")
    if not document.title.strip():
        missing.append("title")
    if not document.doc_version.strip() or document.doc_version.strip().casefold() in {"", "unknown", "none"}:
        missing.append("date_or_version")
    page_count = document.page_count or len(document.pages)
    if page_count < 1:
        missing.append("page_count")
    if missing:
        reasons.append("missing required metadata: " + ", ".join(missing))

    text = "\n".join(page.text for page in document.pages if isinstance(page.text, str))
    lower = text.casefold()
    truncation_markers = ("[truncated]", "<truncated>", "content truncated", "page not found")
    if any(marker in lower for marker in truncation_markers):
        reasons.append("text contains an explicit truncation marker")
    elif path.suffix.lower() == ".pdf" and path.stat().st_size >= 100_000 and len(text.strip()) < 100:
        reasons.append("text is suspiciously truncated relative to the PDF file size")

    return {"status": "pass" if not reasons else "fail", "reasons": reasons or ["required metadata and text completeness verified"]}


def _recency(
    document: Document,
    candidates: Iterable[Mapping[str, object]],
) -> dict[str, Any]:
    newer: list[Mapping[str, object]] = []
    current_key = _version_key(document.doc_version)
    for candidate in candidates:
        candidate_source = str(candidate.get("file", candidate.get("source", "")))
        candidate_title = str(candidate.get("name", candidate.get("title", "")))
        if not _family_match(
            document.title,
            document.source,
            candidate_title,
            candidate_source,
        ):
            continue
        if candidate_source == document.source:
            continue
        candidate_version = str(candidate.get("doc_version", ""))
        if _version_key(candidate_version) > current_key:
            newer.append(candidate)

    if document.superseded:
        matches = ", ".join(
            str(item.get("file", item.get("name", "unknown"))) for item in newer
        ) or "a newer version"
        return {
            "status": "fail",
            "reasons": [f"document is superseded; newer version exists: {matches}"],
            "superseded": True,
        }
    if newer:
        matches = ", ".join(
            f"{item.get('file', item.get('name', 'unknown'))} "
            f"(version {_version_key(str(item.get('doc_version', 'unknown')))})"
            for item in newer
        )
        return {
            "status": "fail",
            "reasons": [f"newer version exists: {matches}"],
            "superseded": False,
        }
    return {
        "status": "pass",
        "reasons": ["no newer version of this document was identified"],
        "superseded": False,
    }


class DocumentVerifier:
    def __init__(
        self,
        *,
        quarantine_dir: Path,
        report_dir: Path,
        archive_dir: Path,
        near_duplicate_threshold: float,
        embedder: Callable[[str], list[float]] | None,
        candidates: Sequence[Mapping[str, object]],
    ) -> None:
        if not 0.0 <= near_duplicate_threshold <= 1.0:
            raise ValueError("near duplicate threshold must be between 0 and 1")
        self.quarantine_dir = quarantine_dir
        self.report_dir = report_dir
        self.archive_dir = archive_dir
        self.threshold = near_duplicate_threshold
        self.embedder = embedder
        self.candidates = candidates
        self.raw_hashes: dict[str, str] = {}
        self.text_hashes: dict[str, str] = {}
        self.embeddings: dict[str, list[float]] = {}
        self.documents: list[dict[str, object]] = []

    def verify(self, path: Path, document: Document) -> dict[str, object]:
        quality = _quality(document)
        completeness = _completeness(path, document)
        recency = _recency(document, self.candidates)

        text = "\n".join(page.text for page in document.pages if isinstance(page.text, str))
        raw_hash = _sha256(path) if path.is_file() else hashlib.sha256(b"").hexdigest()
        text_hash = _text_hash(text)
        exact_match: str | None = None
        for other_source, other_hash in self.raw_hashes.items():
            if raw_hash == other_hash:
                exact_match = other_source
                break
        if exact_match is None:
            for other_source, other_hash in self.text_hashes.items():
                if text_hash and text_hash == other_hash:
                    exact_match = other_source
                    break

        near_match: str | None = None
        near_similarity = 0.0
        embedding_error = ""
        embedding: list[float] | None = None
        if text.strip() and self.embedder is not None:
            try:
                embedding = self.embedder(text)
                for other_source, other_embedding in self.embeddings.items():
                    similarity = _cosine(embedding, other_embedding)
                    if similarity >= self.threshold and similarity > near_similarity:
                        near_match = other_source
                        near_similarity = similarity
            except Exception as exc:
                embedding_error = f"{type(exc).__name__}: {exc}"
        elif text.strip() and self.embedder is None:
            embedding_error = "embedding service was not configured"

        uniqueness_reasons: list[str] = []
        exact_status = "pass"
        near_status = "pass"
        if exact_match:
            exact_status = "fail"
            uniqueness_reasons.append(f"exact duplicate of {exact_match}")
        else:
            uniqueness_reasons.append("no exact duplicate detected")
        if embedding_error:
            near_status = "fail"
            uniqueness_reasons.append(f"near-duplicate check failed: {embedding_error}")
        elif near_match:
            near_status = "fail"
            uniqueness_reasons.append(
                f"near duplicate of {near_match} at similarity {near_similarity:.6f}"
            )
        else:
            uniqueness_reasons.append("no near duplicate detected")

        uniqueness = {
            "status": "pass" if exact_status == "pass" and near_status == "pass" else "fail",
            "reasons": uniqueness_reasons,
            "exact_duplicate": {
                "status": exact_status,
                "matching_document": exact_match,
                "content_hash": raw_hash,
            },
            "near_duplicate": {
                "status": near_status,
                "matching_document": near_match,
                "similarity": near_similarity if near_match else None,
                "threshold": self.threshold,
            },
        }

        overall_pass = all(
            check["status"] == "pass"
            for check in (quality, completeness, recency, uniqueness)
        )
        result: dict[str, object] = {
            "source": document.source,
            "title": document.title,
            "doc_version": document.doc_version,
            "page_count": document.page_count or len(document.pages),
            "raw_sha256": raw_hash,
            "text_sha256": text_hash,
            "checks": {
                "quality": quality,
                "completeness": completeness,
                "recency": recency,
                "uniqueness": uniqueness,
            },
            "decision": "quarantine" if not overall_pass else "index",
        }

        if overall_pass:
            self.raw_hashes[document.source] = raw_hash
            self.text_hashes[document.source] = text_hash
            if embedding is not None:
                self.embeddings[document.source] = embedding

        if not overall_pass:
            self.quarantine_dir.mkdir(parents=True, exist_ok=True)
            if bool(recency.get("superseded")):
                from .incremental import ArchiveManager

                archive_path = ArchiveManager(self.archive_dir).archive_document(
                    path,
                    document.doc_version,
                )
                result["archive_path"] = str(archive_path)
            destination = self._copy_to(path, self.quarantine_dir)
            if _sha256(destination) != raw_hash:
                destination.unlink(missing_ok=True)
                raise RuntimeError(
                    f"quarantine copy verification failed for {path}"
                )
            result["quarantine_path"] = str(destination)
            try:
                path.unlink()
            except OSError as exc:
                if (
                    exc.errno not in {errno.EACCES, errno.EPERM, errno.EROFS}
                    and getattr(exc, "winerror", None) != 5
                ):
                    raise
                result["quarantine_action"] = "verified_copy_source_retained"
                result["quarantine_source_retained_reason"] = str(exc)
            else:
                result["quarantine_action"] = "moved_after_verified_copy"

        self.documents.append(result)
        return result

    def _copy_to(self, path: Path, directory: Path) -> Path:
        destination = directory / path.name
        if destination.exists():
            source_hash = _sha256(path)
            if _sha256(destination) == source_hash:
                return destination
            destination = directory / f"{path.stem}.{source_hash[:12]}{path.suffix}"
            if destination.exists() and _sha256(destination) == source_hash:
                return destination
            if destination.exists():
                suffix = uuid.uuid4().hex[:8]
                destination = directory / f"{path.stem}.{source_hash[:12]}.{suffix}{path.suffix}"
        shutil.copy2(path, destination)
        return destination

    def write_report(self, run_id: str) -> Path:
        from datetime import datetime, timezone

        self.report_dir.mkdir(parents=True, exist_ok=True)
        quarantined = sum(
            1 for document in self.documents if document["decision"] == "quarantine"
        )
        report = {
            "run_id": run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "near_duplicate_threshold": self.threshold,
            "summary": {
                "total": len(self.documents),
                "index": len(self.documents) - quarantined,
                "quarantine": quarantined,
                "overall_decision": "quarantine_failures" if quarantined else "index_all",
            },
            "documents": self.documents,
        }
        report_path = self.report_dir / f"verification-{run_id}.json"
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return report_path
