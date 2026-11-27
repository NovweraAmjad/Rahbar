"""Summarize query traces over a UTC time window."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from .trace_tools import parse_timestamp, read_jsonl, summarize_records
except ImportError:
    from trace_tools import parse_timestamp, read_jsonl, summarize_records


def _required_time(value: str) -> datetime:
    parsed = parse_timestamp(value)
    if parsed is None:
        raise argparse.ArgumentTypeError(
            "timestamp must be ISO-8601 with an explicit UTC offset"
        )
    return parsed.astimezone(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace_file", type=Path)
    parser.add_argument("--since", required=True, type=_required_time)
    parser.add_argument("--until", required=True, type=_required_time)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.since >= args.until:
        parser.error("--since must be earlier than --until")

    try:
        records, malformed = read_jsonl(args.trace_file)
    except OSError as exc:
        print(f"trace summary failed: {exc}", file=sys.stderr)
        return 2
    summary = summarize_records(records, malformed, args.since, args.until)
    encoded = json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"could not write trace summary: {exc}", file=sys.stderr)
            return 2
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
