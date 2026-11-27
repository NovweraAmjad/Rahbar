"""Pipeline metrics shared by the answer service and /metrics route."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

ANSWERS = Counter("rahbar_answers_total", "Answer outcomes", ["outcome"])
TOP1_SIMILARITY = Histogram(
    "rahbar_top1_retrieval_similarity",
    "Top-1 retrieval similarity",
    buckets=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
)
STAGE_LATENCY = Histogram(
    "rahbar_pipeline_stage_seconds",
    "Pipeline stage latency in seconds",
    ["stage"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)
INJECTION_ATTEMPTS = Counter(
    "rahbar_injection_attempts_flagged_total",
    "Queries with prompt-injection content detected",
)
