"""The retriever interface. The rest of the API only ever sees this."""

from __future__ import annotations

from typing import Protocol

from ..schemas import Passage


class RetrievalResults(list[Passage]):
    """List-compatible retrieval output with request-local trace metadata."""

    def __init__(
        self, passages: list[Passage], trace_metadata: dict[str, object] | None = None
    ) -> None:
        super().__init__(passages)
        self.trace_metadata = trace_metadata or {}


class Retriever(Protocol):
    name: str

    def retrieve(
        self,
        question: str,
        k: int,
        query_embedding: list[float] | None = None,
    ) -> list[Passage]:
        """Return up to k passages, best first."""
        ...
