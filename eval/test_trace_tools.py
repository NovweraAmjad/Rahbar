from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from eval.trace_inspect import TraceInspectionError, inspect_trace
from eval.trace_tools import read_jsonl, summarize_records


def _trace(
    trace_id: str = "trace-1",
    *,
    timestamp: str = "2026-11-22T13:00:00Z",
    cache: dict[str, object] | None = None,
) -> dict[str, object]:
    prompt_payload = {
        "model": "llama-test",
        "messages": [{"role": "user", "content": "Exact prompt"}],
    }
    return {
        "trace_id": trace_id,
        "timestamp": timestamp,
        "original_query": "What is the requirement?",
        "context": [{"chunk_id": "chunk-1", "text": "Exact context"}],
        "prompt_payload": prompt_payload,
        "prompt_hash": hashlib.sha256(
            json.dumps(
                prompt_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "generation": {"sent": True},
        "response": {"answered_from_context": True},
        "retrieval": {"top1_similarity": 0.8},
        "stage_latency_ms": {"retrieval": 20.0, "generation": 100.0},
        "guardrail": {"detected": False},
        "cache": cache or {"status": "miss", "source_trace_id": None},
    }


def test_trace_inspection_verifies_prompt_hash_and_printable_context() -> None:
    trace = _trace()

    inspected = inspect_trace([trace], "trace-1")

    assert inspected["context"] == trace["context"]
    assert inspected["prompt_payload"] == trace["prompt_payload"]
    assert inspected["source_trace_id"] == "trace-1"


def test_trace_inspection_follows_cache_hit_to_original_generation() -> None:
    source = _trace("source")
    cached = _trace("cached", cache={"status": "hit", "source_trace_id": "source"})
    cached["context"] = []
    cached["prompt_payload"] = None
    cached["generation"] = {"sent": False}

    inspected = inspect_trace([cached, source], "cached")

    assert inspected["requested_trace_id"] == "cached"
    assert inspected["source_trace_id"] == "source"
    assert inspected["context"] == source["context"]


def test_trace_inspection_rejects_missing_exact_prompt() -> None:
    trace = _trace()
    trace["prompt_payload"] = None

    with pytest.raises(TraceInspectionError, match="no saved prompt"):
        inspect_trace([trace], "trace-1")


def test_jsonl_loader_counts_malformed_and_non_trace_records(tmp_path) -> None:
    path = tmp_path / "queries.jsonl"
    path.write_text(
        '{"trace_id":"valid","timestamp":"2026-11-22T13:00:00Z"}\n'
        '{"broken":\n'
        '{"event":"other"}\n',
        encoding="utf-8",
    )
    path.write_bytes(path.read_bytes() + b"\xff\n")

    records, malformed = read_jsonl(path)

    assert [record["trace_id"] for record in records] == ["valid"]
    assert malformed == 3


def test_summary_calculates_metrics_and_skips_missing_or_invalid_values() -> None:
    start = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
    end = datetime(2026, 11, 22, 14, tzinfo=timezone.utc)
    valid = _trace()
    valid["response"] = {"answered_from_context": False}
    valid["guardrail"] = {"detected": True}
    invalid = _trace("bad", timestamp="not-a-timestamp")
    invalid["retrieval"] = {}

    result = summarize_records([valid, invalid], malformed=2, start=start, end=end)

    assert result["records"] == {
        "valid_in_window": 1,
        "malformed": 2,
        "invalid_timestamps": 1,
    }
    assert result["top1_similarity"]["p95"] == pytest.approx(0.8)
    assert result["abstention_rate"]["rate"] == 1.0
    assert result["cache_hit_rate"]["rate"] == 0.0
    assert result["cache_hit_rate"]["status_counts"] == {"miss": 1}
    assert result["missing_metrics"]["cache_status"] == 0
    assert result["stage_latency_ms"]["generation"]["p95"] == pytest.approx(100.0)
    assert result["injection_attempts_flagged"] == 1
