"""Print the exact saved prompt and context for a query trace."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

try:
    from .trace_tools import read_jsonl
except ImportError:
    from trace_tools import read_jsonl


class TraceInspectionError(ValueError):
    pass


def _find_trace(records: list[dict[str, Any]], trace_id: str) -> dict[str, Any]:
    matches = [record for record in records if record["trace_id"] == trace_id]
    if not matches:
        raise TraceInspectionError(f"trace ID {trace_id!r} was not found")
    if len(matches) != 1:
        raise TraceInspectionError(f"trace ID {trace_id!r} occurs more than once")
    return matches[0]


def inspect_trace(records: list[dict[str, Any]], trace_id: str) -> dict[str, Any]:
    record = _find_trace(records, trace_id)
    seen: set[str] = set()
    while True:
        current_id = record["trace_id"]
        if current_id in seen:
            raise TraceInspectionError("cache trace references contain a cycle")
        seen.add(current_id)
        cache = record.get("cache")
        if cache is not None and not isinstance(cache, dict):
            raise TraceInspectionError("cache trace metadata is invalid")
        source_id = cache.get("source_trace_id") if isinstance(cache, dict) else None
        if not source_id:
            break
        if not isinstance(source_id, str):
            raise TraceInspectionError("cache source trace ID is invalid")
        record = _find_trace(records, source_id)

    if not isinstance(record.get("context"), list):
        raise TraceInspectionError("trace has no saved context; reconstruction is impossible")
    prompt_payload = record.get("prompt_payload")
    response = record.get("response")
    generation = record.get("generation")
    generated = isinstance(generation, dict) and generation.get("sent") is True
    if generated and not isinstance(prompt_payload, dict):
        raise TraceInspectionError("generated answer has no saved prompt payload")
    if isinstance(prompt_payload, dict):
        expected_hash = record.get("prompt_hash")
        actual_hash = hashlib.sha256(
            json.dumps(
                prompt_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if expected_hash != actual_hash:
            raise TraceInspectionError("saved prompt payload does not match its hash")
    elif prompt_payload is not None:
        raise TraceInspectionError("saved prompt payload has an invalid shape")

    return {
        "requested_trace_id": trace_id,
        "source_trace_id": record["trace_id"],
        "timestamp": record.get("timestamp"),
        "original_query": record.get("original_query"),
        "context": record["context"],
        "prompt_payload": prompt_payload,
        "prompt_hash": record.get("prompt_hash"),
        "prompt_status": "exact saved provider payload" if generated else (
            "no provider prompt was sent; generation was skipped"
        ),
        "response": response,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace_id", help="trace ID to inspect")
    parser.add_argument(
        "--trace-file", type=Path, default=Path("traces/queries.jsonl")
    )
    args = parser.parse_args(argv)
    try:
        records, malformed = read_jsonl(args.trace_file)
        result = inspect_trace(records, args.trace_id)
    except (OSError, TraceInspectionError) as exc:
        print(f"trace inspection failed: {exc}", file=sys.stderr)
        return 2
    if malformed:
        print(f"Skipped {malformed} malformed JSONL record(s).", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
