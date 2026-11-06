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
from pathlib import Path

from .config import IndexerSettings, get_settings
from .models import Chunk


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

    index = root / "_index.json"
    if index.exists():
        entries = json.loads(index.read_text(encoding="utf-8"))
        return [(root / e["file"], str(e.get("doc_version", "unknown")),
                 bool(e.get("superseded", False))) for e in entries]
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

    documents = discover(settings.corpus_dir)
    if not documents:
        raise RuntimeError(f"no documents found in {settings.corpus_dir}")
    log(
        "documents discovered",
        count=len(documents),
        sources=[path.name for path, _version, _superseded in documents],
    )

    all_chunks: list[Chunk] = []
    for path, doc_version, superseded in documents:
        stage = "extract"
        try:
            doc = extract(str(path), doc_version, superseded)
            if not doc.pages:
                raise RuntimeError("extraction produced no pages")
            log(
                "extracted",
                source=path.name,
                pages=len(doc.pages),
                text_pages=sum(
                    isinstance(page.text, str) and bool(page.text.strip()) for page in doc.pages
                ),
                ocr_pages=sum(page.ocr for page in doc.pages),
            )

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the RAHBAR index, then exit.")
    parser.add_argument("--dummy", action="store_true", help="write three fake chunks")
    args = parser.parse_args()
    settings = get_settings()
    log("indexer starting", dummy=args.dummy, embed_model=settings.embed_model,
        collection=settings.collection, chroma=f"{settings.chroma_host}:{settings.chroma_port}",
        pod=os.environ.get("HOSTNAME", "-"))
    started = time.perf_counter()
    try:
        if args.dummy:
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
