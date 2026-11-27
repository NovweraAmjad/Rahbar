#!/usr/bin/env python3
"""Run end-to-end RAHBAR answer evaluation against the real API."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import time
from typing import Any

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import run_eval  # noqa: E402


class EndToEndError(RuntimeError):
    pass


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        raise EndToEndError("cannot calculate percentile of an empty list")
    if not 0.0 <= percentile_value <= 100.0:
        raise EndToEndError("percentile must be between 0 and 100")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile_value / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def citation_is_valid(citation: dict[str, Any], retrieved: list[dict[str, Any]]) -> bool:
    for passage in retrieved:
        if citation.get("source") != passage.get("source"):
            continue
        if citation.get("page") is not None and citation.get("page") != passage.get("page"):
            continue
        citation_section = citation.get("section")
        passage_section = passage.get("section")
        if citation_section and passage_section:
            if " ".join(str(citation_section).split()).casefold() != " ".join(
                str(passage_section).split()
            ).casefold():
                continue
        return True
    return False


def context_support_status(response: dict[str, Any]) -> str:
    """Conservative faithfulness label based on the actual API response."""
    if not response.get("answered_from_context"):
        return "not_answered"
    citations = response.get("citations")
    retrieved = response.get("retrieved")
    if not isinstance(citations, list) or not isinstance(retrieved, list):
        return "unsupported"
    if not citations or not retrieved:
        return "unsupported"
    valid_citations = sum(
        citation_is_valid(citation, retrieved)
        for citation in citations
        if isinstance(citation, dict)
    )
    if valid_citations == len(citations):
        return "faithful"
    return "unsupported"


def abstention_status(question: dict[str, Any], response: dict[str, Any]) -> bool | None:
    if question["expected_answerability"] != "must_abstain":
        return None
    return (
        not response["answered_from_context"]
        and not response["citations"]
    )


def aggregate_end_to_end(rows: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [row for row in rows if row["request_status"] == "succeeded"]
    failures = [row for row in rows if row["request_status"] == "failed"]
    answerable = [
        row for row in successful
        if row["expected_answerability"] == "answerable"
    ]
    abstentions = [
        row for row in successful
        if row["expected_answerability"] == "must_abstain"
    ]

    faithful = sum(row["context_support_status"] == "faithful" for row in answerable)
    unsupported = sum(row["context_support_status"] == "unsupported" for row in answerable)
    not_answered = sum(row["context_support_status"] == "not_answered" for row in answerable)

    citation_count = sum(row["citation_count"] for row in successful)
    valid_citation_count = sum(row["valid_citation_count"] for row in successful)

    abstention_correct = sum(bool(row["abstention_correct"]) for row in abstentions)
    latencies = [row["latency_seconds"] for row in successful]

    failed_answerable = [
        row for row in failures
        if row["expected_answerability"] == "answerable"
    ]
    failed_abstentions = [
        row for row in failures
        if row["expected_answerability"] == "must_abstain"
    ]
    faithfulness = (
        None
        if failed_answerable
        else (100.0 * faithful / len(answerable) if answerable else None)
    )
    abstention_correctness = (
        None
        if failed_abstentions
        else (
            100.0 * abstention_correct / len(abstentions)
            if abstentions
            else None
        )
    )
    citation_validity = (
        100.0 * valid_citation_count / citation_count
        if citation_count
        else None
    )
    p50 = percentile(latencies, 50.0) if latencies else None
    p95 = percentile(latencies, 95.0) if latencies else None

    return {
        "total_questions": len(rows),
        "successful_requests": len(successful),
        "request_failures": len(failures),
        "faithfulness": {
            "method": "conservative_citation_context_check",
            "answerable_count": len(answerable),
            "faithful_count": faithful,
            "unsupported_count": unsupported,
            "not_answered_count": not_answered,
            "percentage": faithfulness,
        },
        "abstention_correctness": {
            "unanswerable_count": len(abstentions),
            "correct_count": abstention_correct,
            "percentage": abstention_correctness,
        },
        "citation_validity": {
            "citation_count": citation_count,
            "valid_citation_count": valid_citation_count,
            "percentage": citation_validity,
        },
        "latency": {
            "p50_seconds": p50,
            "p95_seconds": p95,
            "request_count": len(successful),
        },
    }


def validate_output(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise EndToEndError("evaluation output must be a JSON object")
    required_top = {"evaluation_timestamp", "api_base_url", "provider", "metrics", "questions"}
    if not required_top.issubset(payload):
        raise EndToEndError("evaluation output is missing required fields")
    metrics = payload["metrics"]
    if not isinstance(metrics, dict):
        raise EndToEndError("metrics must be a JSON object")
    if not isinstance(payload["questions"], list):
        raise EndToEndError("questions must be a JSON list")
    for name in ("faithfulness", "abstention_correctness", "citation_validity", "latency"):
        if name not in metrics:
            raise EndToEndError(f"metrics missing {name}")
    return payload


def run_evaluation(
    *,
    api: str,
    host: str,
    golden: Path,
    timeout: float,
    output_root: Path,
) -> Path:
    from eval.golden_schema import load_golden_jsonl

    questions = load_golden_jsonl(golden)
    timestamp = datetime.now(timezone.utc)
    run_id = timestamp.strftime("%Y%m%dT%H%M%SZ")
    output_dir = output_root / f"end-to-end-{run_id}"
    if output_dir.exists():
        raise EndToEndError(f"refusing to overwrite existing output: {output_dir}")

    headers = {"Host": host} if host else {}
    rows: list[dict[str, Any]] = []
    provider_names: set[str] = set()
    try:
        with httpx.Client(timeout=timeout, headers=headers) as client:
            for question in questions:
                started = time.perf_counter()
                try:
                    response = run_eval.ask(client, api, question["question"])
                    error = None
                    provider_names.add(response["provider"])
                except run_eval.EvaluationRequestError as exc:
                    response = None
                    error = str(exc)
                elapsed = time.perf_counter() - started

                row: dict[str, Any] = {
                    "id": question["id"],
                    "category": question["category"],
                    "question": question["question"],
                    "expected_answerability": question["expected_answerability"],
                    "gold_evidence": question["gold_evidence"],
                    "origin": question["origin"],
                    "manual_review": question["manual_review"],
                    "request_status": "failed" if error else "succeeded",
                    "request_error": error,
                    "latency_seconds": elapsed,
                }
                if response is None:
                    row.update(
                        {
                            "answer": None,
                            "answered_from_context": None,
                            "citations": [],
                            "retrieved": [],
                            "provider": None,
                            "context_support_status": "request_failed",
                            "abstention_correct": abstention_status(question, {
                                "answered_from_context": False,
                                "citations": [],
                            }) if question["expected_answerability"] == "must_abstain" else None,
                            "citation_count": 0,
                            "valid_citation_count": 0,
                        }
                    )
                else:
                    citations = response["citations"]
                    retrieved = response["retrieved"]
                    valid_citation_count = sum(
                        citation_is_valid(citation, retrieved)
                        for citation in citations
                        if isinstance(citation, dict)
                    )
                    row.update(
                        {
                            "request_id": response["request_id"],
                            "answer": response["answer"],
                            "answered_from_context": response["answered_from_context"],
                            "confidence": response["confidence"],
                            "citations": citations,
                            "retrieved": retrieved,
                            "provider": response["provider"],
                            "context_support_status": context_support_status(response),
                            "abstention_correct": abstention_status(question, response),
                            "citation_count": len(citations),
                            "valid_citation_count": valid_citation_count,
                        }
                    )
                rows.append(row)
    except httpx.HTTPError as exc:
        raise EndToEndError(f"unable to initialize API client: {exc}") from exc

    if not provider_names and not all(
        row["request_status"] == "failed" for row in rows
    ):
        raise EndToEndError("real provider identity was not observed")

    output_dir.mkdir(parents=True, exist_ok=False)
    metrics = aggregate_end_to_end(rows)
    payload: dict[str, Any] = {
        "evaluation_timestamp": timestamp.isoformat(),
        "api_base_url": api,
        "golden_file": str(golden.resolve()),
        "provider": (
            sorted(provider_names)[0]
            if len(provider_names) == 1
            else sorted(provider_names)
        ),
        "metrics": metrics,
        "questions": rows,
    }
    validate_output(payload)
    (output_dir / "summary.json").write_text(
        json.dumps(payload["metrics"], ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "per-question.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run real RAHBAR end-to-end answer evaluation"
    )
    parser.add_argument("--api", default="http://localhost:8080")
    parser.add_argument("--host", default="rahbar.local")
    parser.add_argument("--golden", type=Path, default=HERE / "golden.jsonl")
    parser.add_argument("--out-root", type=Path, default=HERE / "results")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    if args.timeout <= 0:
        print("error: --timeout must be positive", file=sys.stderr)
        return 2
    try:
        output_dir = run_evaluation(
            api=args.api,
            host=args.host,
            golden=args.golden,
            timeout=args.timeout,
            output_root=args.out_root,
        )
    except (EndToEndError, run_eval.GoldenSetError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps({"status": "ok", "output_dir": str(output_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
