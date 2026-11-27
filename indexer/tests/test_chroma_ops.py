from __future__ import annotations

from typing import Any

import pytest

from pipeline import chroma_ops
from pipeline.config import get_settings


class FakeCollection:
    def __init__(self) -> None:
        self.updated: list[dict[str, Any]] = []
        self.upsert_called = False

    def get(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "ids": ["chunk-1", "chunk-2"],
            "metadatas": [
                {"source": "policy.pdf", "superseded": False},
                {"source": "policy.pdf", "superseded": False},
            ],
        }

    def update(self, **kwargs: Any) -> None:
        self.updated.append(kwargs)

    def upsert(self, **_kwargs: Any) -> None:
        self.upsert_called = True


def test_mark_superseded_updates_existing_metadata_without_upserting(monkeypatch) -> None:
    collection = FakeCollection()
    monkeypatch.setattr(chroma_ops, "get_collection", lambda _settings: collection)

    count = chroma_ops.mark_superseded(get_settings(), ["chunk-1", "chunk-2"])

    assert count == 2
    assert collection.updated == [
        {
            "ids": ["chunk-1", "chunk-2"],
            "metadatas": [
                {"source": "policy.pdf", "superseded": True},
                {"source": "policy.pdf", "superseded": True},
            ],
        }
    ]
    assert collection.upsert_called is False


def test_orphan_check_surfaces_chroma_read_failures(monkeypatch) -> None:
    class UnavailableCollection:
        def get(self, **_kwargs: Any) -> dict[str, Any]:
            raise OSError("Chroma is unavailable")

    monkeypatch.setattr(
        chroma_ops,
        "get_collection",
        lambda _settings: UnavailableCollection(),
    )

    with pytest.raises(RuntimeError, match="Unable to list chunks"):
        chroma_ops.verify_no_orphans(get_settings(), {"policy.pdf"})
