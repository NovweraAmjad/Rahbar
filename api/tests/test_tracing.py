from __future__ import annotations

import json

from app.tracing import write_trace


def test_write_trace_appends_one_jsonl_record_and_emits_structured_log(
    tmp_path, caplog
) -> None:
    trace_file = tmp_path / "traces" / "queries.jsonl"
    record = {
        "trace_id": "trace-123",
        "timestamp": "2026-11-22T13:00:00Z",
        "context": [{"chunk_id": "chunk-1", "text": "Exact context"}],
        "prompt_payload": {"messages": [{"role": "user", "content": "Exact prompt"}]},
    }

    with caplog.at_level("INFO", logger="rahbar.answer"):
        write_trace(record, str(trace_file))

    lines = trace_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == record
    log_record = next(item for item in caplog.records if item.getMessage() == "query_trace")
    assert log_record.trace_id == "trace-123"
