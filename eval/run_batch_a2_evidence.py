"""Run Part A/B ingestion evidence against a live Chroma and Ollama service."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import io
import json
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

import chromadb
import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "indexer"))
sys.path.insert(0, str(ROOT / "api"))

from app.config import Settings as ApiSettings  # noqa: E402
from app.retrieval.chroma import ChromaRetriever  # noqa: E402
from pipeline.chroma_ops import (  # noqa: E402
    get_all_chunks_by_source,
    get_chunks_by_source,
    get_collection,
)
from pipeline.config import get_settings  # noqa: E402
from pipeline.embed import embed  # noqa: E402
from pipeline.incremental import IncrementalIndexer  # noqa: E402
from pipeline.run import (  # noqa: E402
    _manifest_metadata,
    restore_version,
    run_incremental_pipeline,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _service_facts(host: str, port: int, ollama_url: str, model: str) -> dict[str, Any]:
    try:
        chroma_client = chromadb.HttpClient(host=host, port=port)
        chroma_version = chroma_client.get_version()
    except Exception as exc:
        raise RuntimeError(
            f"Live Chroma is unavailable at {host}:{port}: {type(exc).__name__}: {exc}"
        ) from exc

    try:
        response = httpx.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=10.0)
        response.raise_for_status()
        tags_payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(
            f"Ollama is unavailable at {ollama_url!r}: {type(exc).__name__}: {exc}"
        ) from exc

    models = tags_payload.get("models") if isinstance(tags_payload, dict) else None
    if not isinstance(models, list):
        raise RuntimeError("Ollama /api/tags did not return a models list")
    model_tag = next(
        (
            item
            for item in models
            if isinstance(item, dict)
            and str(item.get("name", "")).split(":")[0] == model.split(":")[0]
        ),
        None,
    )
    if model_tag is None:
        raise RuntimeError(f"configured Ollama embedding model is unavailable: {model}")

    return {
        "chroma": {
            "host": host,
            "port": port,
            "version": chroma_version,
        },
        "embedding_provider": {
            "url": ollama_url,
            "model": model,
            "model_tag": model_tag,
        },
    }


def _settings(
    corpus_dir: Path,
    work_dir: Path,
    collection: str,
    base: Any,
    *,
    chunk_size: int = 400,
) -> Any:
    return replace(
        base,
        corpus_dir=str(corpus_dir),
        indexer_state_dir=str(work_dir / "state"),
        quarantine_dir=str(work_dir / "quarantine"),
        verification_report_dir=str(work_dir / "verification-reports"),
        archive_dir=str(work_dir / "archive"),
        collection=collection,
        chunk_size=chunk_size,
        chunk_overlap=50 if chunk_size > 50 else 5,
        redis_url="",
    )


def _run_pipeline(label: str, settings: Any, evidence_dir: Path) -> dict[str, Any]:
    log = io.StringIO()
    with redirect_stdout(log):
        result = run_incremental_pipeline(settings)
    (evidence_dir / f"{label}.log").write_text(log.getvalue(), encoding="utf-8")
    _write_json(evidence_dir / f"{label}.json", result)
    return result


def _latest_verification_report(settings: Any) -> Path:
    reports = list(Path(settings.verification_report_dir).glob("verification-*.json"))
    if not reports:
        raise RuntimeError("the incremental run did not produce a verification report")
    return max(reports, key=lambda report: report.stat().st_mtime_ns)


def _source_snapshot(settings: Any, source: str) -> list[dict[str, Any]]:
    result = get_collection(settings).get(
        where={"source": source},
        include=["documents", "metadatas"],
    )
    rows = []
    for chunk_id, document, metadata in zip(
        result["ids"],
        result["documents"] or [],
        result["metadatas"] or [],
    ):
        rows.append(
            {
                "id": chunk_id,
                "document": document,
                "metadata": metadata,
            }
        )
    return sorted(rows, key=lambda row: row["id"])


def _corpus_snapshot(settings: Any) -> dict[str, list[dict[str, Any]]]:
    sources = get_all_chunks_by_source(settings)
    return {source: _source_snapshot(settings, source) for source in sorted(sources)}


def _add_lifecycle_documents(corpus: Path) -> tuple[str, str]:
    changed_source = "part-a-integration-change.txt"
    deleted_source = "part-a-integration-delete.txt"
    changed_file = corpus / changed_source
    deleted_file = corpus / deleted_source
    changed_file.write_text(
        "A temporary indexing policy is retained for controlled updates.\n",
        encoding="utf-8",
    )
    deleted_file.write_text(
        "A temporary source is used to verify document-level cleanup.\n",
        encoding="utf-8",
    )

    index_path = corpus / "_index.json"
    entries = json.loads(index_path.read_text(encoding="utf-8"))
    entries.extend(
        [
            {
                "name": "Part A Integration Change",
                "url": "integration://part-a/change",
                "file": changed_source,
                "doc_version": "2026-11",
                "fetched_at": "2026-11-22",
                "superseded": False,
            },
            {
                "name": "Part A Integration Delete",
                "url": "integration://part-a/delete",
                "file": deleted_source,
                "doc_version": "2026-11",
                "fetched_at": "2026-11-22",
                "superseded": False,
            },
        ]
    )
    _write_json(index_path, entries)
    return changed_source, deleted_source


def _run_batch_a(run_dir: Path, collection: str, base: Any) -> dict[str, Any]:
    source = ROOT / "corpus" / "raw"
    corpus = run_dir / "corpus"
    work = run_dir / "work"
    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, corpus)
    changed_source, deleted_source = _add_lifecycle_documents(corpus)
    settings = _settings(corpus, work, collection, base)
    initial = _run_pipeline("initial-index", settings, run_dir)
    verification = json.loads(
        _latest_verification_report(settings).read_text(encoding="utf-8")
    )
    _write_json(run_dir / "initial-verification.json", verification)
    quarantined = {
        item["source"]
        for item in verification["documents"]
        if item["decision"] == "quarantine"
    }
    expected_quarantine = {
        "undergraduate-academic-rules-aug-2023.pdf",
        "annual-report-2019-20.pdf",
        "faculty-entrepreneurship-and-technical-start-ups-policy.pdf",
    }
    if quarantined != expected_quarantine:
        raise AssertionError(
            "Batch A verification decisions differ from the corpus expectations: "
            f"{sorted(quarantined)}"
        )

    collection_client = get_collection(settings)
    initial_snapshot = _corpus_snapshot(settings)
    initial_count = int(collection_client.count())
    if initial_count != int(initial["total_stored"]):
        raise AssertionError(
            f"Batch A Chroma count {initial_count} != reported total {initial['total_stored']}"
        )
    if initial_count != sum(len(rows) for rows in initial_snapshot.values()):
        raise AssertionError("Batch A source snapshots do not match the Chroma count")

    unchanged = _run_pipeline("unchanged-rerun", settings, run_dir)
    if unchanged["chunks_embedded"] != 0:
        raise AssertionError(
            f"unchanged Batch A rerun embedded {unchanged['chunks_embedded']} chunks"
        )

    changed_file = corpus / changed_source
    changed_file.write_text(
        "This updated policy changes the target document. " * 90,
        encoding="utf-8",
    )
    deleted_file = corpus / deleted_source
    deleted_file.unlink()
    manifest_path = corpus / "_index.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = [item for item in manifest if item.get("file") != deleted_source]
    _write_json(manifest_path, manifest)

    updated = _run_pipeline("changed-and-deleted-rerun", settings, run_dir)
    updated_snapshot = _corpus_snapshot(settings)
    if updated["chunks_embedded"] <= 0:
        raise AssertionError("changed Batch A document did not embed any chunks")
    if updated_snapshot.get(deleted_source):
        raise AssertionError("deleted Batch A document still has Chroma chunks")
    if not updated_snapshot.get(changed_source):
        raise AssertionError("changed Batch A document has no Chroma chunks")
    if len(updated_snapshot[changed_source]) != updated["chunks_embedded"]:
        raise AssertionError(
            "changed Batch A Chroma chunks do not match the newly embedded chunks"
        )
    for source_name, snapshot in initial_snapshot.items():
        if source_name not in {changed_source, deleted_source}:
            if updated_snapshot.get(source_name) != snapshot:
                raise AssertionError(
                    f"unrelated Batch A document changed: {source_name}"
                )

    persisted = IncrementalIndexer(Path(settings.indexer_state_dir))
    changed_state = persisted.get_state_for_source(changed_source)
    if changed_state is None or changed_state.file_hash != _sha256(changed_file):
        raise AssertionError("updated Batch A state did not survive reloading")
    if persisted.get_state_for_source(deleted_source) is not None:
        raise AssertionError("deleted Batch A state was not removed")
    final_count = int(collection_client.count())
    if final_count != sum(len(rows) for rows in updated_snapshot.values()):
        raise AssertionError("final Batch A source snapshots do not match Chroma count")

    return {
        "collection": collection,
        "initial_count": initial_count,
        "unchanged_rerun": {
            "chunks_embedded": unchanged["chunks_embedded"],
            "chunks_skipped": unchanged["chunks_skipped"],
        },
        "updated_and_deleted": {
            "chunks_embedded": updated["chunks_embedded"],
            "chunks_removed": updated["chunks_removed"],
            "changed_source_chunks": len(updated_snapshot[changed_source]),
            "deleted_source_chunk_count": len(
                get_chunks_by_source(settings, deleted_source)
            ),
            "unrelated_sources_unchanged": True,
        },
        "persistent_state_reloaded": True,
        "final_collection_count": final_count,
        "final_chunks_by_source": {
            source_name: len(chunks)
            for source_name, chunks in updated_snapshot.items()
        },
    }


def _run_batch_b(run_dir: Path, collection: str, base: Any) -> dict[str, Any]:
    source = ROOT / "corpus" / "batch-b"
    corpus = run_dir / "corpus"
    work = run_dir / "work"
    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, corpus)
    settings = _settings(corpus, work, collection, base)

    first = _run_pipeline("initial-index", settings, run_dir)
    verification = json.loads(
        _latest_verification_report(settings).read_text(encoding="utf-8")
    )
    _write_json(run_dir / "initial-verification.json", verification)

    second = _run_pipeline("unchanged-rerun", settings, run_dir)
    if second["chunks_embedded"] != 0:
        raise AssertionError(
            f"unchanged Batch B rerun embedded {second['chunks_embedded']} chunks"
        )

    decisions = {
        document["source"]: document
        for document in verification["documents"]
    }
    quarantined = {
        source_name
        for source_name, result in decisions.items()
        if result["decision"] == "quarantine"
    }
    chunks_by_source = get_all_chunks_by_source(settings)
    if chunks_by_source.keys() & quarantined:
        raise AssertionError("a quarantined Batch B document was stored in Chroma")
    if "batch-academic-policy-2026.txt" not in chunks_by_source:
        raise AssertionError("current Batch B policy version was not indexed")
    for source_name in quarantined:
        result = decisions[source_name]
        quarantine_path = Path(str(result["quarantine_path"]))
        if not quarantine_path.is_file():
            raise AssertionError(f"missing recoverable quarantine copy: {source_name}")
        if result.get("quarantine_action") == "moved_after_verified_copy":
            if (corpus / source_name).exists():
                raise AssertionError(f"quarantined source was not moved: {source_name}")
        elif result.get("quarantine_action") != "verified_copy_source_retained":
            raise AssertionError(
                f"unknown quarantine disposition for {source_name}: "
                f"{result.get('quarantine_action')!r}"
            )

    exact_check = decisions["batch-exact-b.txt"]["checks"]["uniqueness"][
        "exact_duplicate"
    ]
    if (
        exact_check["status"] != "fail"
        or exact_check["matching_document"] != "batch-exact-a.txt"
    ):
        raise AssertionError(
            "real exact-duplicate verification did not match the planted source: "
            f"{exact_check}"
        )
    near_result = decisions["batch-near-b.txt"]
    near_check = near_result["checks"]["uniqueness"]["near_duplicate"]
    if (
        near_check["status"] != "fail"
        or near_check["matching_document"] != "batch-near-a.txt"
        or float(near_check["similarity"]) < 0.95
    ):
        raise AssertionError(
            "real embedding provider did not identify the planted near duplicate "
            f"at the configured 0.95 threshold: {near_check}"
        )
    if int(get_collection(settings).count()) != int(first["total_stored"]):
        raise AssertionError("Batch B collection count differs from the pipeline report")

    return {
        "collection": collection,
        "first_run": first,
        "unchanged_rerun": {
            "chunks_embedded": second["chunks_embedded"],
            "chunks_skipped": second["chunks_skipped"],
        },
        "verification_summary": verification["summary"],
        "near_duplicate_similarity": near_check["similarity"],
        "quarantined_sources": sorted(quarantined),
        "quarantine_actions": {
            source_name: decisions[source_name]["quarantine_action"]
            for source_name in sorted(quarantined)
        },
        "archive_paths": [
            item["archive_path"]
            for item in decisions.values()
            if item.get("archive_path")
        ],
        "collection_count": int(get_collection(settings).count()),
        "chunks_by_source": {
            source_name: len(chunk_ids)
            for source_name, chunk_ids in chunks_by_source.items()
        },
    }


def _run_superseded_restore(run_dir: Path, collection: str, base: Any) -> dict[str, Any]:
    source_root = ROOT / "corpus" / "batch-b"
    corpus = run_dir / "corpus"
    work = run_dir / "work"
    corpus.mkdir(parents=True)

    old_source = "batch-academic-policy-2025.txt"
    new_source = "batch-academic-policy-2026.txt"
    shutil.copy2(source_root / old_source, corpus / old_source)
    source_entries = json.loads((source_root / "_index.json").read_text(encoding="utf-8"))
    old_entry = next(item for item in source_entries if item["file"] == old_source)
    new_entry = next(item for item in source_entries if item["file"] == new_source)
    initial_old_entry = {**old_entry, "superseded": False}
    _write_json(corpus / "_index.json", [initial_old_entry])
    settings = _settings(corpus, work, collection, base)

    initial = _run_pipeline("old-version-index", settings, run_dir)
    old_before = get_chunks_by_source(settings, old_source)
    if not old_before:
        raise AssertionError("initial version was not stored in real Chroma")

    shutil.copy2(source_root / new_source, corpus / new_source)
    _write_json(
        corpus / "_index.json",
        [{**old_entry, "superseded": True}, new_entry],
    )
    advanced = _run_pipeline("new-version-index", settings, run_dir)
    new_chunks = get_chunks_by_source(settings, new_source)
    if not new_chunks:
        raise AssertionError("current policy version was not indexed in real Chroma")
    archived = sorted(
        Path(settings.archive_dir).glob(f"{Path(old_source).stem}@2025*.txt")
    )
    if not archived:
        raise AssertionError("superseded version was not archived")
    if _sha256(archived[0]) != _sha256(source_root / old_source):
        raise AssertionError("superseded archive does not match the source version")

    collection = get_collection(settings)
    old_rows = collection.get(
        where={"source": old_source},
        include=["metadatas"],
    )
    if not old_rows["metadatas"] or not all(
        metadata.get("superseded") is True
        for metadata in old_rows["metadatas"]
    ):
        raise AssertionError("superseded chunks were not marked in Chroma metadata")

    api_settings = ApiSettings(
        retriever="chroma",
        chroma_host=settings.chroma_host,
        chroma_port=settings.chroma_port,
        collection=settings.collection,
    )
    retriever = ChromaRetriever(api_settings)
    old_query_vector = embed(
        ["The 2025 academic policy requirements."],
        settings,
    )[0]
    before_restore = retriever.retrieve(
        "What did the 2025 academic policy require?",
        k=5,
        query_embedding=old_query_vector,
    )
    if any(passage.source == old_source for passage in before_restore):
        raise AssertionError("retrieval returned archived 2025 chunks before restore")

    restore = restore_version(settings, old_source, str(archived[0]))
    restored_state = IncrementalIndexer(Path(settings.indexer_state_dir))
    old_state = restored_state.get_state_for_source(old_source)
    if old_state is None or old_state.doc_version != "2025" or old_state.archived:
        raise AssertionError("restored version was not persisted as active index state")
    after_restore = retriever.retrieve(
        "What did the 2025 academic policy require?",
        k=5,
        query_embedding=old_query_vector,
    )
    if not any(
        passage.source == old_source and passage.doc_version == "2025"
        for passage in after_restore
    ):
        raise AssertionError("fresh retrieval did not return the restored 2025 version")
    if any(passage.superseded for passage in after_restore):
        raise AssertionError("retrieval returned superseded chunks after restore")
    new_rows_after_restore = collection.get(
        where={"source": new_source},
        include=["metadatas"],
    )
    if not new_rows_after_restore["metadatas"] or not all(
        metadata.get("superseded") is True
        for metadata in new_rows_after_restore["metadatas"]
    ):
        raise AssertionError("the replaced current version remained active after restore")
    new_archives = sorted(
        Path(settings.archive_dir).glob(f"{Path(new_source).stem}@2026*.txt")
    )
    if not new_archives or _sha256(new_archives[0]) != _sha256(corpus / new_source):
        raise AssertionError("the displaced current version was not archived intact")
    if any(passage.source == new_source for passage in after_restore):
        raise AssertionError("fresh retrieval returned the replaced current version")
    restored_state = IncrementalIndexer(Path(settings.indexer_state_dir))
    restored_old_state = restored_state.get_state_for_source(old_source)
    archived_new_state = restored_state.get_state_for_source(new_source)
    if (
        restored_old_state is None
        or restored_old_state.archived
        or archived_new_state is None
        or not archived_new_state.archived
    ):
        raise AssertionError("restored and superseded version states were not persisted")
    restored_candidates = _manifest_metadata(settings.corpus_dir)
    restored_manifest = {
        Path(str(item.get("file", ""))).name: bool(item.get("superseded", False))
        for item in restored_candidates
    }
    if restored_manifest.get(old_source) or not restored_manifest.get(new_source):
        raise AssertionError("version manifest did not persist the restored active version")

    return {
        "collection": collection.name,
        "initial_run": initial,
        "new_version_run": advanced,
        "old_chunks_before_superseding": len(old_before),
        "old_chunks_after_superseding": len(old_rows["ids"]),
        "old_metadata_marked_superseded": True,
        "retrieval_excluded_old_before_restore": True,
        "archive_path": str(archived[0]),
        "displaced_current_archive_path": str(new_archives[0]),
        "restore_result": restore,
        "restored_state_version": old_state.doc_version,
        "new_version_marked_superseded_after_restore": True,
        "restored_manifest_active": True,
        "retrieval_returned_restored_version": True,
        "collection_count_after_restore": int(collection.count()),
    }


def run_evidence(
    output_root: Path,
    *,
    host: str,
    port: int,
    ollama_url: str,
    model: str,
) -> Path:
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    run_dir = output_root / f"part-a-chroma-{run_id}"
    run_dir.mkdir(parents=True, exist_ok=False)

    base = replace(
        get_settings(),
        chroma_host=host,
        chroma_port=port,
        ollama_url=ollama_url,
        embed_model=model,
    )
    run_record: dict[str, Any] = {
        "schema_version": 1,
        "experiment": "assignment-03-part-a-chroma-integration",
        "run_id": run_id,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "service_facts": None,
        "scenarios": {},
        "live_user_collection_modified": False,
        "notes": [
            "Each scenario uses a fresh UUID-named collection.",
            "No Chroma collection is deleted by the evidence runner.",
            "All corpus mutations occur only in copies under this timestamped result directory.",
        ],
    }
    _write_json(run_dir / "run.json", run_record)

    try:
        run_record["service_facts"] = _service_facts(host, port, ollama_url, model)
        run_record["scenarios"]["batch_a"] = _run_batch_a(
            run_dir / "batch-a",
            f"rahbar-pa-{uuid.uuid4().hex[:20]}",
            base,
        )
        run_record["scenarios"]["batch_b"] = _run_batch_b(
            run_dir / "batch-b",
            f"rahbar-pb-{uuid.uuid4().hex[:20]}",
            base,
        )
        run_record["scenarios"]["superseded_restore"] = _run_superseded_restore(
            run_dir / "superseded-restore",
            f"rahbar-pr-{uuid.uuid4().hex[:20]}",
            base,
        )
        run_record["status"] = "completed"
    except Exception as exc:
        run_record["status"] = "failed"
        run_record["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        run_record["completed_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(run_dir / "run.json", run_record)
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run real Chroma/Ollama Part A evidence using isolated collections."
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "eval" / "results",
        help="directory under which a new timestamped result folder is created",
    )
    parser.add_argument(
        "--chroma-host",
        default="127.0.0.1",
        help="host of a running Chroma HTTP service",
    )
    parser.add_argument("--chroma-port", type=int, default=18118)
    parser.add_argument(
        "--ollama-url",
        default="http://172.23.112.1:11434",
        help="URL of the real Ollama embedding provider",
    )
    parser.add_argument("--embedding-model", default="nomic-embed-text")
    args = parser.parse_args()
    run_dir = run_evidence(
        args.output_root,
        host=args.chroma_host,
        port=args.chroma_port,
        ollama_url=args.ollama_url,
        model=args.embedding_model,
    )
    print(f"Part A integration evidence: {run_dir}")
    print(f"Run record: {run_dir / 'run.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
