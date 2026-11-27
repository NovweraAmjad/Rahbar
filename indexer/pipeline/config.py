"""Indexer configuration, from the same ConfigMap the API reads (section 4.1)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IndexerSettings:
    embed_model: str        # EMBED_MODEL: must equal the API's value (section 4.4)
    chroma_host: str
    chroma_port: int
    collection: str
    corpus_dir: str         # where the documents and _index.json sit inside the Job's pod
    chunk_size: int
    chunk_overlap: int
    ollama_url: str
    redis_url: str
    cache_prefix: str
    indexer_state_dir: str
    quarantine_dir: str
    verification_report_dir: str
    archive_dir: str
    near_duplicate_threshold: float
    # QUARANTINE_PROMPT_INJECTION=false keeps documents with injection-like text in the
    # index (flagged in the report) so the API's runtime guardrails can be exercised.
    quarantine_prompt_injection: bool = True


def get_settings() -> IndexerSettings:
    corpus_dir = os.environ.get("CORPUS_DIR", "/corpus")
    return IndexerSettings(
        embed_model=os.environ.get("EMBED_MODEL", "nomic-embed-text"),
        chroma_host=os.environ.get("CHROMA_HOST", "chroma"),
        chroma_port=int(os.environ.get("CHROMA_PORT", 8000)),
        collection=os.environ.get("CHROMA_COLLECTION", "rahbar"),
        corpus_dir=corpus_dir,
        chunk_size=int(os.environ.get("CHUNK_SIZE", 400)),
        chunk_overlap=int(os.environ.get("CHUNK_OVERLAP", 50)),
        ollama_url=os.environ.get("OLLAMA_URL", "http://host.minikube.internal:11434"),
        redis_url=os.environ.get("REDIS_URL", ""),
        cache_prefix=os.environ.get("CACHE_PREFIX", "rahbar:semantic-cache"),
        indexer_state_dir=os.environ.get(
            "INDEXER_STATE_DIR",
            str(Path(corpus_dir) / ".indexer-state"),
        ),
        quarantine_dir=os.environ.get("QUARANTINE_DIR", "/quarantine"),
        verification_report_dir=os.environ.get(
            "VERIFICATION_REPORT_DIR", "/verification-reports"
        ),
        archive_dir=os.environ.get("ARCHIVE_DIR", "/archive"),
        near_duplicate_threshold=float(
            os.environ.get("NEAR_DUPLICATE_THRESHOLD", "0.95")
        ),
        quarantine_prompt_injection=os.environ.get(
            "QUARANTINE_PROMPT_INJECTION", "true"
        ).strip().lower()
        not in {"0", "false", "no", "off"},
    )
