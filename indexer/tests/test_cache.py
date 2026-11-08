from __future__ import annotations

from dataclasses import replace

import pytest
from redis import RedisError

from pipeline.cache import invalidate_semantic_cache
from pipeline.config import get_settings


class FakeRedis:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, ...]] = []
        self.increments: list[str] = []

    def scan_iter(self, *, match: str):
        assert match == "rahbar:answers:rahbar:entry:*"
        return iter(
            [
                "rahbar:answers:rahbar:entry:one",
                "rahbar:answers:other:entry:two",
                "unrelated:rahbar:answers:rahbar:entry:three",
            ]
        )

    def delete(self, *keys: str) -> int:
        self.deleted.append(keys)
        return len(keys)

    def incr(self, key: str) -> int:
        self.increments.append(key)
        return 1

    def close(self) -> None:
        pass


def _settings():
    return replace(
        get_settings(),
        redis_url="redis://redis:6379/0",
        cache_prefix="rahbar:answers",
        collection="rahbar",
    )


def test_invalidation_deletes_only_keys_for_configured_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeRedis()

    def from_url(_url: str, **_kwargs: object) -> FakeRedis:
        return client

    monkeypatch.setattr("pipeline.cache.Redis.from_url", from_url)

    removed = invalidate_semantic_cache(_settings())

    assert removed == 1
    assert client.increments == ["rahbar:answers:rahbar:generation"]
    assert client.deleted == [("rahbar:answers:rahbar:entry:one",)]


def test_invalidation_is_skipped_when_redis_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "pipeline.cache.Redis.from_url",
        lambda *_args, **_kwargs: pytest.fail("Redis should not be constructed"),
    )

    assert invalidate_semantic_cache(replace(_settings(), redis_url="")) == 0


def test_invalidation_failure_aborts_with_an_explicit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnavailableRedis(FakeRedis):
        def scan_iter(self, *, match: str):
            raise RedisError("connection refused")

    monkeypatch.setattr(
        "pipeline.cache.Redis.from_url",
        lambda *_args, **_kwargs: UnavailableRedis(),
    )

    with pytest.raises(RuntimeError, match="Unable to invalidate the semantic cache"):
        invalidate_semantic_cache(_settings())
