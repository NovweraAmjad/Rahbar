"""Optional Redis-backed semantic cache for validated, grounded answers."""

from __future__ import annotations

import json
import logging
import math
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError
from prometheus_client import Counter
from redis import Redis, RedisError

from ..config import Settings
from ..embeddings import embed_query
from ..schemas import AskResponse

log = logging.getLogger("rahbar.cache")

CACHE_LOOKUPS = Counter(
    "rahbar_cache_lookups_total",
    "Semantic cache lookups",
    ["result"],
)

_CACHE_TTL_SECONDS = 24 * 60 * 60
_FOLLOW_UP_START = re.compile(
    r"^(?:and\b|also\b|then\b|what about\b|how about\b|why\b|how\b|"
    r"tell me more\b|can you explain\b|in that case\b)",
    re.IGNORECASE,
)
_REFERENTIAL_TERMS = re.compile(
    r"\b(?:it|they|them|their|that|those|these|this|same)\b",
    re.IGNORECASE,
)


class RedisSemanticClient(Protocol):
    def scan_iter(self, *, match: str) -> Iterable[Any]: ...

    def get(self, key: str) -> Any: ...

    def set(self, key: str, value: str, *, ex: int) -> Any: ...

    def incr(self, key: str) -> Any: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class CacheLookup:
    embedding: list[float] | None
    response: AskResponse | None
    may_store: bool
    generation: str | None


def is_follow_up(question: str) -> bool:
    """Conservatively bypass caching for likely conversational follow-ups."""
    return bool(_FOLLOW_UP_START.search(question.strip()) or _REFERENTIAL_TERMS.search(question))


def _cosine_similarity(left: list[float], right: object) -> float | None:
    if not isinstance(right, list) or len(left) != len(right) or not left:
        return None
    right_values: list[float] = []
    try:
        for value in right:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            number = float(value)
            if not math.isfinite(number):
                return None
            right_values.append(number)
    except OverflowError:
        return None
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right_values))
    if left_norm == 0.0 or right_norm == 0.0:
        return None
    similarity = sum(a * b for a, b in zip(left, right_values)) / (left_norm * right_norm)
    return similarity if math.isfinite(similarity) else None


def _response_matches_its_citations(response: AskResponse) -> bool:
    retrieved = {
        (item.source, item.section, item.page)
        for item in response.retrieved
    }
    return bool(response.citations) and all(
        (citation.source, citation.section, citation.page) in retrieved
        for citation in response.citations
    )


class SemanticCache:
    def __init__(self, client: RedisSemanticClient, settings: Settings) -> None:
        threshold = settings.cache_similarity_threshold
        if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
            raise ValueError("cache_similarity_threshold must be between 0.0 and 1.0")
        self.client = client
        self.settings = settings
        self.key_prefix = f"{settings.cache_prefix}:{settings.collection}:entry:"
        self.generation_key = f"{settings.cache_prefix}:{settings.collection}:generation"

    def lookup(self, question: str) -> CacheLookup:
        if is_follow_up(question):
            CACHE_LOOKUPS.labels(result="bypass").inc()
            return CacheLookup(embedding=None, response=None, may_store=False, generation=None)

        try:
            embedding = embed_query(question, self.settings)
        except (RuntimeError, ValueError) as exc:
            CACHE_LOOKUPS.labels(result="error").inc()
            log.warning("cache query embedding failed; bypassing cache", extra={"error": str(exc)})
            return CacheLookup(embedding=None, response=None, may_store=False, generation=None)

        try:
            generation_value = self.client.get(self.generation_key)
            generation = "0" if generation_value is None else generation_value
            if not isinstance(generation, str) or not generation.isdecimal():
                raise ValueError("Redis returned an invalid cache generation")

            for key in self.client.scan_iter(match=f"{self.key_prefix}{generation}:*"):
                raw_entry = self.client.get(key)
                if not isinstance(raw_entry, str):
                    continue
                try:
                    entry = json.loads(raw_entry)
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("embed_model") != self.settings.embed_model:
                        continue
                    similarity = _cosine_similarity(embedding, entry.get("embedding"))
                    if (
                        similarity is None
                        or similarity < self.settings.cache_similarity_threshold
                    ):
                        continue
                    response = AskResponse.model_validate(entry.get("response"))
                    if not response.answered_from_context or not _response_matches_its_citations(
                        response
                    ):
                        continue
                    current_generation = self.client.get(self.generation_key)
                    if current_generation is None:
                        current_generation = "0"
                    if current_generation != generation:
                        continue
                    CACHE_LOOKUPS.labels(result="hit").inc()
                    log.info(
                        "semantic cache hit",
                        extra={"similarity": round(similarity, 4)},
                    )
                    return CacheLookup(
                        embedding=embedding,
                        response=response,
                        may_store=False,
                        generation=generation,
                    )
                except (ValueError, TypeError, ValidationError):
                    log.warning("ignoring malformed semantic cache entry", extra={"key": str(key)})
            CACHE_LOOKUPS.labels(result="miss").inc()
            return CacheLookup(
                embedding=embedding,
                response=None,
                may_store=True,
                generation=generation,
            )
        except (RedisError, OSError, ValueError) as exc:
            CACHE_LOOKUPS.labels(result="error").inc()
            log.warning("Redis lookup failed; continuing without cache", extra={"error": str(exc)})
            return CacheLookup(embedding=embedding, response=None, may_store=False, generation=None)

    def store(
        self,
        embedding: list[float] | None,
        response: AskResponse,
        *,
        may_store: bool,
        generation: str | None,
    ) -> None:
        if (
            not may_store
            or embedding is None
            or generation is None
            or not response.answered_from_context
            or not _response_matches_its_citations(response)
        ):
            return

        entry = {
            "embed_model": self.settings.embed_model,
            "embedding": embedding,
            "response": response.model_dump(mode="json"),
        }
        try:
            self.client.set(
                f"{self.key_prefix}{generation}:{uuid.uuid4().hex}",
                json.dumps(entry, ensure_ascii=False),
                ex=_CACHE_TTL_SECONDS,
            )
        except (RedisError, OSError) as exc:
            CACHE_LOOKUPS.labels(result="error").inc()
            log.warning("Redis write failed; answer returned uncached", extra={"error": str(exc)})

    def close(self) -> None:
        self.client.close()


def get_semantic_cache(settings: Settings) -> SemanticCache | None:
    if not settings.redis_url.strip():
        return None
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    return SemanticCache(client, settings)
