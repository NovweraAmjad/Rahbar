"""The retriever interface. The rest of the API only ever sees this."""

from __future__ import annotations

from typing import Protocol

from ..schemas import Passage


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
