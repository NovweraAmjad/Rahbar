# ADR 0002: Redis semantic answer cache

## Status
Accepted · 2026-11-06

## Context
Repeated or semantically similar questions incur query-embedding, Chroma retrieval,
and generation costs. Cached answers must retain the API response contract and citations
from the evidence originally retrieved. The assignment requires follow-ups, abstentions,
out-of-scope answers, and invalid answers to bypass caching, and requires invalidation when
the corpus is reindexed.

## Decision
Use optional Redis, disabled when `REDIS_URL` is empty. Cache entries are namespaced by
`CACHE_PREFIX` and Chroma collection, expire after 24 hours, and contain the embedding
model name, query vector, and validated `AskResponse`. A hit requires matching embedding
models and cosine similarity at or above `CACHE_SIMILARITY_THRESHOLD`. The default
threshold is `0.95`, a conservative provisional setting, not a measured or guaranteed
false-hit-free value.

The API embeds an eligible query once and passes that vector to Chroma on a cache miss.
Likely follow-ups bypass both lookup and storage using a conservative lexical heuristic;
the API currently has no conversation-state input with which to identify every follow-up.
Only context-grounded responses with citations are stored. Redis errors fail open and
leave normal retrieval and generation available. The indexer increments a collection
generation and deletes only keys for the configured cache prefix and collection before
changing Chroma; entries written by requests already in flight remain in the old
generation and cannot be returned. If invalidation fails, index writes are stopped.

An offline 20-pair threshold probe using the configured `nomic-embed-text:latest`
model found one false hit at 0.90 and none at 0.93, 0.95, or 0.97. The lowest tested
zero-false-hit threshold was 0.93, but it is not promoted: the probe is a small
pair-level sample rather than a production answer-correctness evaluation. The
ConfigMap remains at 0.95 pending broader validation.

## Options considered
- Chroma metadata or vector search: rejected because the cache is mutable answer state,
  not corpus evidence, and should not share collection lifecycle or retrieval semantics.
- Redis exact-query keys: rejected because they do not reuse answers for semantically
  equivalent wording.
- Redis-backed vector search: not selected for this milestone; direct cosine comparison
  avoids introducing vector-index configuration for a small cache.

## Consequences
Redis remains optional for local development. The simple scan-and-compare implementation
is suitable only while the cache is modest; high entry counts may require an indexed
vector-search strategy. The offline threshold probe and its limitations are documented
in `eval/README.md`; more representative, answer-reviewed pairs are needed before any
threshold change. Corpus invalidation is scoped and occurs before index mutation, but
operational configuration must provide the same Redis URL, cache prefix, and collection
to the API and indexer.
