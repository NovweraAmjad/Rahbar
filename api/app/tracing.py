"""Persistent JSONL query traces and trace-record normalization."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("rahbar.answer")


def write_trace(record: dict[str, Any], trace_file: str = "") -> None:
    """Write one complete trace to the configured file and structured stdout log."""
    encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if trace_file:
        path = Path(trace_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o640)
        try:
            payload = (encoded + "\n").encode("utf-8")
            written = os.write(descriptor, payload)
            if written != len(payload):
                raise OSError(f"short trace write: wrote {written} of {len(payload)} bytes")
        finally:
            os.close(descriptor)
    log.info("query_trace", extra=record)


def normalize_trace_record(value: object) -> dict[str, Any] | None:
    """Accept raw trace JSON and the API's structured JSON log wrapper."""
    if not isinstance(value, dict):
        return None
    nested = value.get("trace_record")
    if isinstance(nested, dict):
        value = nested
    if not isinstance(value.get("trace_id"), str):
        return None
    return value
