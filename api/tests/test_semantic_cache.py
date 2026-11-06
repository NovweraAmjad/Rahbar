from __future__ import annotations

import fnmatch
import json
from unittest.mock import Mock

import pytest
from redis import RedisError

from app.config import Settings
from app.logjson import request_id_var
from app.schemas import AskResponse, Citation, Passage, RetrievedRef
from app.services.answer import answer_question
from app.services.semantic_cache import SemanticCache, is_follow_up


class MemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.fail_scan = False

    def scan_iter(self, *, match: str):
        if self.fail_scan:
            raise RedisError("Redis unavailable")
        return (key for key in self.values if fnmatch.fnmatch(key, match))

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str, *, ex: int) -> bool:
        assert ex > 0
        self.values[key] = value
        return True

    def incr(self, key: str) -> int:
        value = int(self.values.get(key, "0")) + 1
        self.values[key] = str(value)
        return value

    def delete(self, *keys: str) -> int:
        return sum(self.values.pop(key, None) is not None for key in keys)

    def close(self) -> None:
        pass


def _settings(*, cache_similarity_threshold: float = 0.95) -> Settings:
    return Settings(
        redis_url="redis://localhost:6379/0",
        cache_similarity_threshold=cache_similarity_threshold,
    )


def _response() -> AskResponse:
    return AskResponse(
        request_id="previous-request",
        answer="Students need at least 80% attendance.",
        citations=[
            Citation(source="academic-rules.pdf", section="1. Attendance", page=5)
        ],
        answered_from_context=True,
        confidence="high",
        retrieved=[
            RetrievedRef(
                chunk_id="rules-1",
                source="academic-rules.pdf",
                section="1. Attendance",
                page=5,
                score=0.91,
            )
        ],
        provider="test-provider",
    )


def _passage() -> Passage:
    return Passage(
        chunk_id="rules-1",
        text="Students need at least 80% attendance.",
        source="academic-rules.pdf",
        section="1. Attendance",
        page=5,
        score=0.91,
        doc_version="2026",
    )


def _store(
    cache: SemanticCache,
    response: AskResponse | None = None,
    *,
    generation: str = "0",
) -> None:
    cache.store(
        [1.0, 0.0],
        response or _response(),
        may_store=True,
        generation=generation,
    )


def _cache(
    monkeypatch: pytest.MonkeyPatch,
    *,
    redis: MemoryRedis | None = None,
    vector: list[float] | None = None,
    settings: Settings | None = None,
) -> tuple[SemanticCache, MemoryRedis, Mock]:
    client = redis or MemoryRedis()
    embedding = vector or [1.0, 0.0]
    embed = Mock(return_value=embedding)
    monkeypatch.setattr("app.services.semantic_cache.embed_query", embed)
    return SemanticCache(client, settings or _settings()), client, embed


def test_cache_miss_returns_embedding_for_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    cache, redis, embed = _cache(monkeypatch)

    result = cache.lookup("What attendance is required?")

    assert result.response is None
    assert result.embedding == [1.0, 0.0]
    assert result.may_store is True
    assert result.generation == "0"
    embed.assert_called_once_with("What attendance is required?", cache.settings)
    assert redis.values == {}


def test_semantic_cache_hit_returns_validated_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    cache, _, embed = _cache(monkeypatch)
    _store(cache)

    result = cache.lookup("What attendance is required?")

    assert result.response == _response()
    assert result.embedding == [1.0, 0.0]
    assert result.may_store is False
    embed.assert_called_once()


@pytest.mark.parametrize(
    ("threshold", "incoming", "expected_hit"),
    [
        (0.95, [0.8, 0.6], False),
        (0.8, [0.8, 0.6], True),
    ],
)
def test_similarity_threshold_is_applied(
    monkeypatch: pytest.MonkeyPatch,
    threshold: float,
    incoming: list[float],
    expected_hit: bool,
) -> None:
    cache, _, _ = _cache(
        monkeypatch,
        vector=incoming,
        settings=_settings(cache_similarity_threshold=threshold),
    )
    _store(cache)

    result = cache.lookup("A different question?")

    assert (result.response is not None) is expected_hit


def test_different_question_with_low_similarity_is_a_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache, _, _ = _cache(monkeypatch, vector=[0.0, 1.0])
    _store(cache)

    assert cache.lookup("What are the library hours?").response is None


@pytest.mark.parametrize(
    "question",
    [
        "What about next semester?",
        "Can you explain it?",
        "Tell me more about that.",
    ],
)
def test_follow_up_questions_bypass_cache(
    monkeypatch: pytest.MonkeyPatch, question: str
) -> None:
    cache, redis, embed = _cache(monkeypatch)

    result = cache.lookup(question)

    assert is_follow_up(question)
    assert result.embedding is None
    assert result.may_store is False
    embed.assert_not_called()
    assert redis.values == {}


@pytest.mark.parametrize(
    "response",
    [
        AskResponse(
            request_id="id",
            answer="No relevant evidence.",
            citations=[],
            answered_from_context=False,
            confidence="low",
            retrieved=[],
            provider="test-provider",
        ),
        _response().model_copy(update={"answered_from_context": False}),
        _response().model_copy(update={"citations": []}),
    ],
)
def test_cache_excludes_abstentions_and_ungrounded_answers(
    monkeypatch: pytest.MonkeyPatch, response: AskResponse
) -> None:
    cache, redis, _ = _cache(monkeypatch)

    _store(cache, response)

    assert redis.values == {}


def test_redis_failure_fails_open_and_disables_cache_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = MemoryRedis()
    redis.fail_scan = True
    cache, _, _ = _cache(monkeypatch, redis=redis)

    result = cache.lookup("What attendance is required?")

    assert result.response is None
    assert result.embedding == [1.0, 0.0]
    assert result.may_store is False


def test_cache_hit_skips_retrieval_and_generation_and_uses_current_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache, _, _ = _cache(monkeypatch)
    _store(cache)
    retriever = Mock()
    provider = Mock()
    token = request_id_var.set("current-request")
    try:
        result = answer_question(
            "What attendance is required?",
            retriever,
            provider,
            _settings(),
            cache,
        )
    finally:
        request_id_var.reset(token)

    assert result.request_id == "current-request"
    assert result.answer == _response().answer
    retriever.retrieve.assert_not_called()
    provider.generate.assert_not_called()


def test_cache_miss_reuses_query_embedding_and_stores_grounded_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache, redis, _ = _cache(monkeypatch)
    retriever = Mock()
    retriever.name = "test-retriever"
    retriever.retrieve.return_value = [_passage()]
    provider = Mock()
    provider.name = "test-provider"
    provider.generate.return_value = {
        "reasoning": "Evidence says 80%.",
        "answer": "Students need at least 80% attendance.",
        "citations": [
            {"source": "academic-rules.pdf", "section": "1. Attendance", "page": 5}
        ],
        "answered_from_context": True,
        "confidence": "high",
    }

    result = answer_question(
        "What attendance is required?",
        retriever,
        provider,
        _settings(),
        cache,
    )

    assert result.answered_from_context
    retriever.retrieve.assert_called_once_with(
        "What attendance is required?",
        k=5,
        query_embedding=[1.0, 0.0],
    )
    assert len(redis.values) == 1
    stored = json.loads(next(iter(redis.values.values())))
    assert stored["embed_model"] == "nomic-embed-text"
    assert stored["embedding"] == [1.0, 0.0]
    assert stored["response"]["answer"] == result.answer


def test_invalidation_generation_prevents_late_stale_cache_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache, redis, _ = _cache(monkeypatch)
    previous_lookup = cache.lookup("What attendance is required?")
    assert previous_lookup.generation == "0"

    redis.incr(cache.generation_key)
    cache.store(
        previous_lookup.embedding,
        _response(),
        may_store=previous_lookup.may_store,
        generation=previous_lookup.generation,
    )

    current_lookup = cache.lookup("What attendance is required?")

    assert current_lookup.response is None
    assert current_lookup.generation == "1"
    assert any(":entry:0:" in key for key in redis.values)
