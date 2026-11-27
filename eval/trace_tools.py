"""Shared JSONL trace loading and metric calculations for trace CLIs."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def normalize_record(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    nested = value.get("trace_record")
    if isinstance(nested, dict):
        value = nested
    if not isinstance(value, dict) or not isinstance(value.get("trace_id"), str):
        return None
    return value


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    malformed = 0
    with path.open("rb") as handle:
        for raw_line in handle:
            if not raw_line.strip():
                continue
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError:
                malformed += 1
                continue
            try:
                record = normalize_record(json.loads(line))
            except json.JSONDecodeError:
                malformed += 1
                continue
            if record is None:
                malformed += 1
                continue
            records.append(record)
    return records, malformed


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def summarize_records(
    records: list[dict[str, Any]], malformed: int, start: datetime, end: datetime
) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    invalid_timestamps = 0
    for record in records:
        timestamp = parse_timestamp(record.get("timestamp"))
        if timestamp is None:
            invalid_timestamps += 1
        elif start <= timestamp < end:
            selected.append(record)

    top1: list[float] = []
    abstentions: list[bool] = []
    cache_lookups: list[bool] = []
    cache_status_counts: dict[str, int] = {}
    stage_values: dict[str, list[float]] = {}
    injection_count = 0
    missing: dict[str, int] = {
        "top1_similarity": 0,
        "abstention": 0,
        "cache_status": 0,
        "injection_status": 0,
    }

    for record in selected:
        retrieval = record.get("retrieval")
        score = finite_number(
            retrieval.get("top1_similarity") if isinstance(retrieval, dict) else None
        )
        if score is None:
            missing["top1_similarity"] += 1
        else:
            top1.append(score)

        response = record.get("response")
        answered = response.get("answered_from_context") if isinstance(response, dict) else None
        if isinstance(answered, bool):
            abstentions.append(not answered)
        else:
            missing["abstention"] += 1

        cache = record.get("cache")
        cache_status = cache.get("status") if isinstance(cache, dict) else None
        if isinstance(cache_status, str):
            cache_status_counts[cache_status] = cache_status_counts.get(cache_status, 0) + 1
        if cache_status in {"hit", "miss"}:
            cache_lookups.append(cache_status == "hit")
        elif not isinstance(cache_status, str):
            missing["cache_status"] += 1

        guardrail = record.get("guardrail")
        detected = guardrail.get("detected") if isinstance(guardrail, dict) else None
        if isinstance(detected, bool):
            injection_count += int(detected)
        else:
            missing["injection_status"] += 1

        latencies = record.get("stage_latency_ms")
        if isinstance(latencies, dict):
            for stage, raw_value in latencies.items():
                value = finite_number(raw_value)
                if value is not None and value >= 0:
                    stage_values.setdefault(str(stage), []).append(value)

    return {
        "window": {
            "start_inclusive": start.isoformat().replace("+00:00", "Z"),
            "end_exclusive": end.isoformat().replace("+00:00", "Z"),
        },
        "records": {
            "valid_in_window": len(selected),
            "malformed": malformed,
            "invalid_timestamps": invalid_timestamps,
        },
        "top1_similarity": {
            "count": len(top1),
            "mean": sum(top1) / len(top1) if top1 else None,
            "min": min(top1) if top1 else None,
            "p50": percentile(top1, 0.5),
            "p95": percentile(top1, 0.95),
            "max": max(top1) if top1 else None,
        },
        "abstention_rate": {
            "count": len(abstentions),
            "rate": sum(abstentions) / len(abstentions) if abstentions else None,
        },
        "cache_hit_rate": {
            "lookup_count": len(cache_lookups),
            "rate": sum(cache_lookups) / len(cache_lookups) if cache_lookups else None,
            "status_counts": dict(sorted(cache_status_counts.items())),
        },
        "stage_latency_ms": {
            stage: {
                "count": len(values),
                "p95": percentile(values, 0.95),
            }
            for stage, values in sorted(stage_values.items())
        },
        "injection_attempts_flagged": injection_count,
        "missing_metrics": missing,
    }
