"""Invalidate API semantic-cache entries before changing the indexed corpus."""

from __future__ import annotations

from typing import cast

from redis import Redis, RedisError

from .config import IndexerSettings


def invalidate_semantic_cache(settings: IndexerSettings) -> int:
    if not settings.redis_url.strip():
        return 0

    prefix = f"{settings.cache_prefix}:{settings.collection}:entry:"
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    removed = 0
    try:
        generation_key = f"{settings.cache_prefix}:{settings.collection}:generation"
        client.incr(generation_key)
        batch: list[str] = []
        for key in client.scan_iter(match=f"{prefix}*"):
            if not isinstance(key, str) or not key.startswith(prefix):
                continue
            batch.append(key)
            if len(batch) == 500:
                removed += cast(int, client.delete(*batch))
                batch.clear()
        if batch:
            removed += cast(int, client.delete(*batch))
        return removed
    except (RedisError, OSError) as exc:
        raise RuntimeError(
            "Unable to invalidate the semantic cache before updating the index"
        ) from exc
    finally:
        client.close()
