from __future__ import annotations

import os
import sys
import uuid
from dataclasses import replace
from pathlib import Path

import chromadb
import pytest

from pipeline.chroma_ops import (
    delete_chunks_by_ids,
    get_chunks_by_source,
    get_collection,
    mark_superseded,
    verify_no_orphans,
)
from pipeline.config import get_settings
from pipeline.models import Chunk
from pipeline.store import store


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))

from app.config import Settings as ApiSettings  # noqa: E402
from app.retrieval.chroma import ChromaRetriever  # noqa: E402


@pytest.fixture
def real_chroma_settings():
    if os.environ.get("RAHBAR_CHROMA_INTEGRATION") != "1":
        pytest.skip("set RAHBAR_CHROMA_INTEGRATION=1 to use a real Chroma service")

    host = os.environ.get("CHROMA_HOST", "127.0.0.1")
    port = int(os.environ.get("CHROMA_PORT", "18118"))
    settings = replace(
        get_settings(),
        chroma_host=host,
        chroma_port=port,
        collection=f"rahbar-it-{uuid.uuid4().hex[:20]}",
        redis_url="",
    )
    client = chromadb.HttpClient(host=host, port=port)
    client.get_version()
    yield settings
    try:
        client.delete_collection(settings.collection)
    except chromadb.errors.InvalidCollectionException:
        pass


def _chunk(
    chunk_id: str,
    source: str,
    text: str,
    version: str,
) -> Chunk:
    return Chunk(
        id=chunk_id,
        text=text,
        source=source,
        section="Integration test policy",
        page=1,
        doc_version=version,
        superseded=False,
    )


@pytest.mark.integration
def test_real_chroma_replaces_and_deletes_only_target_document(
    real_chroma_settings,
) -> None:
    settings = real_chroma_settings
    original = [
        _chunk("policy:2025:1:1", "policy.txt", "Old policy text.", "2025"),
        _chunk("policy:2025:1:2", "policy.txt", "Old policy continuation.", "2025"),
        _chunk("other:2025:1:1", "other.txt", "Unchanged document.", "2025"),
    ]
    store(original, [[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]], settings)
    collection = get_collection(settings)
    initial_count = collection.count()

    replacement = _chunk(
        "policy:2026:1:1",
        "policy.txt",
        "Replacement policy text.",
        "2026",
    )
    store([replacement], [[0.8, 0.2]], settings)
    replaced_ids = [
        chunk_id
        for chunk_id in get_chunks_by_source(settings, "policy.txt")
        if chunk_id != replacement.id
    ]
    delete_chunks_by_ids(settings, replaced_ids)

    policy_chunks = get_chunks_by_source(settings, "policy.txt")
    other_chunks = get_chunks_by_source(settings, "other.txt")
    assert set(policy_chunks) == {replacement.id}
    assert set(other_chunks) == {"other:2025:1:1"}
    assert collection.count() == initial_count + 1 - len(replaced_ids)

    delete_chunks_by_ids(settings, list(other_chunks))
    assert get_chunks_by_source(settings, "other.txt") == {}
    orphan_check = verify_no_orphans(settings, {"policy.txt"})
    assert orphan_check["has_orphans"] is False
    assert orphan_check["chunks_by_source"] == {"policy.txt": 1}


@pytest.mark.integration
def test_real_chroma_superseded_metadata_excludes_old_version_from_retrieval(
    real_chroma_settings,
) -> None:
    settings = real_chroma_settings
    old = _chunk(
        "rules:2025:1:1",
        "academic-rules.txt",
        "The 2025 policy requires a GPA of 2.00 for continuation.",
        "2025",
    )
    current = _chunk(
        "rules:2026:1:1",
        "academic-rules.txt",
        "The 2026 policy requires a GPA of 2.25 for continuation.",
        "2026",
    )
    store([old, current], [[1.0, 0.0], [0.0, 1.0]], settings)

    assert mark_superseded(settings, [old.id]) == 1
    old_result = get_collection(settings).get(
        ids=[old.id],
        include=["metadatas"],
    )
    assert old_result["metadatas"][0]["superseded"] is True

    api_settings = ApiSettings(
        retriever="chroma",
        chroma_host=settings.chroma_host,
        chroma_port=settings.chroma_port,
        collection=settings.collection,
    )
    retriever = ChromaRetriever(api_settings)
    passages = retriever.retrieve(
        "Which GPA did the 2025 policy require?",
        k=5,
        query_embedding=[1.0, 0.0],
    )
    assert passages
    assert all(not passage.superseded for passage in passages)
    assert all(passage.doc_version != "2025" for passage in passages)
