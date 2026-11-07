#!/usr/bin/env python3
"""Evaluate a running RAHBAR API against a YAML golden set."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import yaml  # type: ignore[import-untyped]

HERE = Path(__file__).resolve().parent
DEFAULT_GOLDEN = HERE / "golden.yaml"
DEFAULT_BASE_URL = "http://localhost:8080"
DEFAULT_HOST = "rahbar.local"
REQUEST_TIMEOUT_SECONDS = 120.0
MAX_RECALL5_DROP = 0.05
MIN_CITATION_VALIDITY_PERCENTAGE = 95.0
REQUIRED_ABSTENTION_PERCENTAGE = 100.0

CATEGORY_COUNTS = {
    "ordinary": 8,
    "version-sensitive": 2,
    "exact-term": 2,
    "must-abstain": 2,
    "prompt-injection": 1,
}
CATEGORIES = frozenset(CATEGORY_COUNTS)
_LEGACY_CATEGORIES = {
    "ordinary": "ordinary",
    "version": "version-sensitive",
    "version-sensitive": "version-sensitive",
    "exact": "exact-term",
    "exact-term": "exact-term",
    "abstain": "must-abstain",
    "must-abstain": "must-abstain",
    "injection": "prompt-injection",
    "prompt-injection": "prompt-injection",
}


class GoldenSetError(ValueError):
    """Raised when a golden dataset is missing required fields or evidence."""


class EvaluationRequestError(RuntimeError):
    """Raised for failed requests or responses that violate the API contract."""


def _normalize_section(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return " ".join(value.split()).casefold()


def _normalize_question(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise GoldenSetError(f"question {index} must be a mapping")

    question_id = raw.get("id")
    category_value = raw.get("category", raw.get("type"))
    category: str | None
    if category_value == "injection" and "category" not in raw:
        category = "ordinary"
    else:
        category = (
            _LEGACY_CATEGORIES.get(category_value)
            if isinstance(category_value, str)
            else None
        )
    question = raw.get("question")
    if not isinstance(question_id, str) or not question_id.strip():
        raise GoldenSetError(f"question {index} is missing a non-empty id")
    if category is None:
        raise GoldenSetError(f"{question_id}: unknown category {category_value!r}")
    if not isinstance(question, str) or not 3 <= len(question) <= 500:
        raise GoldenSetError(f"{question_id}: question must contain 3 to 500 characters")

    answerability = raw.get("expected_answerability")
    if answerability is None:
        answerability = (
            "must_abstain"
            if raw.get("must_abstain") or category in {"must-abstain", "prompt-injection"}
            else "answerable"
        )
    if not isinstance(answerability, str) or answerability not in {
        "answerable",
        "must_abstain",
    }:
        raise GoldenSetError(f"{question_id}: invalid expected_answerability")
    must_abstain_category = category in {"must-abstain", "prompt-injection"}
    if must_abstain_category != (answerability == "must_abstain"):
        raise GoldenSetError(
            f"{question_id}: category {category!r} conflicts with its expected answerability"
        )

    evidence = raw.get("gold_evidence", raw.get("relevant", []))
    if not isinstance(evidence, list):
        raise GoldenSetError(f"{question_id}: gold_evidence must be a list")
    normalized_evidence: list[dict[str, Any]] = []
    for evidence_index, item in enumerate(evidence, start=1):
        if not isinstance(item, dict):
            raise GoldenSetError(
                f"{question_id}: gold evidence item {evidence_index} must be a mapping"
            )
        source = item.get("source")
        if not isinstance(source, str) or not source.strip():
            raise GoldenSetError(
                f"{question_id}: gold evidence item {evidence_index} has no source filename"
            )
        page = item.get("page")
        if page is not None and (
            isinstance(page, bool) or not isinstance(page, int) or page < 1
        ):
            raise GoldenSetError(
                f"{question_id}: gold evidence item {evidence_index} has an invalid page"
            )
        section = item.get("section")
        if section is not None and (not isinstance(section, str) or not section.strip()):
            raise GoldenSetError(
                f"{question_id}: gold evidence item {evidence_index} has an invalid section"
            )
        normalized_item: dict[str, Any] = {"source": source.strip()}
        if page is not None:
            normalized_item["page"] = page
        if section is not None:
            normalized_item["section"] = section.strip()
        document_version = item.get("document_version")
        if document_version is not None:
            if not isinstance(document_version, str) or not document_version.strip():
                raise GoldenSetError(
                    f"{question_id}: gold evidence item {evidence_index} has an invalid version"
                )
            normalized_item["document_version"] = document_version.strip()
        normalized_evidence.append(normalized_item)

    if answerability == "answerable" and not normalized_evidence:
        raise GoldenSetError(f"{question_id}: answerable questions require gold_evidence")
    if answerability == "must_abstain" and normalized_evidence:
        raise GoldenSetError(f"{question_id}: must-abstain questions cannot have gold_evidence")

    normalized = {
        "id": question_id.strip(),
        "category": category,
        "question": question,
        "expected_answerability": answerability,
        "gold_evidence": normalized_evidence,
    }
    for optional_field in ("expected_answer", "expected_document", "expected_version"):
        value = raw.get(optional_field, raw.get("expect") if optional_field == "expected_answer" else None)
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise GoldenSetError(f"{question_id}: {optional_field} must be a non-empty string")
            normalized[optional_field] = value.strip()
    if category == "version-sensitive" and "category" in raw:
        if not normalized.get("expected_document") or not normalized.get("expected_version"):
            raise GoldenSetError(
                f"{question_id}: version-sensitive questions must name the expected document and version"
            )
    return normalized


def validate_questions(
    raw_questions: Any,
    *,
    require_assignment_counts: bool = False,
    allowed_sources: set[str] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(raw_questions, list) or not raw_questions:
        raise GoldenSetError("golden YAML must contain a non-empty questions list")
    questions = [_normalize_question(item, index) for index, item in enumerate(raw_questions, 1)]
    ids = [question["id"] for question in questions]
    if len(set(ids)) != len(ids):
        raise GoldenSetError("question IDs must be unique")
    if allowed_sources is not None:
        for question in questions:
            for evidence in question["gold_evidence"]:
                if evidence["source"] not in allowed_sources:
                    raise GoldenSetError(
                        f"{question['id']}: gold evidence source {evidence['source']!r} "
                        "is not listed in corpus/MANIFEST.md"
                    )
    if require_assignment_counts:
        counts = Counter(question["category"] for question in questions)
        if dict(counts) != CATEGORY_COUNTS:
            raise GoldenSetError(
                "golden category counts must be exactly "
                + ", ".join(f"{category}={count}" for category, count in CATEGORY_COUNTS.items())
                + f"; got {dict(counts)}"
            )
    return questions


def _manifest_sources() -> set[str]:
    manifest_path = HERE.parent / "corpus" / "MANIFEST.md"
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise GoldenSetError(f"cannot read corpus source manifest {manifest_path}: {exc}") from exc
    sources: set[str] = set()
    for line in lines:
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) > 4:
            source = cells[4].strip("`")
            if source.endswith(".pdf"):
                sources.add(source)
    if not sources:
        raise GoldenSetError(f"no PDF source filenames found in {manifest_path}")
    return sources


def load_golden(path: Path, *, require_assignment_counts: bool = False) -> list[dict[str, Any]]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GoldenSetError(f"cannot read golden YAML {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise GoldenSetError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GoldenSetError(f"{path} must contain a YAML mapping")
    return validate_questions(
        data.get("questions"),
        require_assignment_counts=require_assignment_counts,
        allowed_sources=_manifest_sources() if require_assignment_counts else None,
    )


def _validate_response(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise EvaluationRequestError("API response must be a JSON object")
    if not isinstance(response.get("request_id"), str) or not response["request_id"]:
        raise EvaluationRequestError("API response is missing a valid request_id")
    if not isinstance(response.get("answer"), str):
        raise EvaluationRequestError("API response is missing a string answer")
    if not isinstance(response.get("answered_from_context"), bool):
        raise EvaluationRequestError("API response is missing boolean answered_from_context")
    if response.get("confidence") not in {"high", "medium", "low"}:
        raise EvaluationRequestError("API response has an invalid confidence value")
    if not isinstance(response.get("provider"), str) or not response["provider"]:
        raise EvaluationRequestError("API response is missing a valid provider")

    for field in ("citations", "retrieved"):
        if not isinstance(response.get(field), list):
            raise EvaluationRequestError(f"API response field {field!r} must be a list")
    for index, citation in enumerate(response["citations"], start=1):
        if not isinstance(citation, dict):
            raise EvaluationRequestError(f"citation {index} must be an object")
        if not isinstance(citation.get("source"), str) or not isinstance(
            citation.get("section"), str
        ):
            raise EvaluationRequestError(f"citation {index} is missing source or section")
        page = citation.get("page")
        if page is not None and (isinstance(page, bool) or not isinstance(page, int) or page < 1):
            raise EvaluationRequestError(f"citation {index} has an invalid page")
    for index, passage in enumerate(response["retrieved"], start=1):
        if not isinstance(passage, dict):
            raise EvaluationRequestError(f"retrieved reference {index} must be an object")
        if not all(
            isinstance(passage.get(field), str) and passage[field]
            for field in ("chunk_id", "source", "section")
        ):
            raise EvaluationRequestError(
                f"retrieved reference {index} is missing chunk_id, source, or section"
            )
        page = passage.get("page")
        if page is not None and (isinstance(page, bool) or not isinstance(page, int) or page < 1):
            raise EvaluationRequestError(f"retrieved reference {index} has an invalid page")
        score = passage.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
            raise EvaluationRequestError(f"retrieved reference {index} has an invalid score")
    return response


def ask(client: httpx.Client, api: str, question: str) -> dict[str, Any]:
    """POST one question and validate the actual AskResponse wire shape."""
    try:
        response = client.post(f"{api.rstrip('/')}/api/ask", json={"question": question})
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise EvaluationRequestError("API request timed out") from exc
    except httpx.HTTPStatusError as exc:
        raise EvaluationRequestError(
            f"API returned HTTP {exc.response.status_code}"
        ) from exc
    except httpx.RequestError as exc:
        raise EvaluationRequestError(f"API request failed ({type(exc).__name__})") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise EvaluationRequestError("API returned malformed JSON") from exc
    return _validate_response(payload)


def _matches(candidate: dict[str, Any], gold: dict[str, Any]) -> bool:
    if candidate.get("source") != gold["source"]:
        return False
    if "page" in gold and candidate.get("page") != gold["page"]:
        return False
    if "section" in gold and _normalize_section(candidate.get("section")) != _normalize_section(
        gold["section"]
    ):
        return False
    return True


def recall_at_k(
    gold_evidence: list[dict[str, Any]],
    retrieved: list[dict[str, Any]],
    k: int,
) -> float | None:
    """Return matched gold evidence fraction; page and provided section must match."""
    if k < 1:
        raise ValueError("k must be positive")
    if not gold_evidence:
        return None
    top_k = retrieved[:k]
    hits = sum(any(_matches(passage, item) for passage in top_k) for item in gold_evidence)
    return hits / len(gold_evidence)


def citation_provenance_validity(
    citations: list[dict[str, Any]],
    retrieved: list[dict[str, Any]],
) -> tuple[int, list[dict[str, Any]]]:
    """Count citations whose source/page/available section maps to a retrieved ref."""
    valid_count = 0
    details: list[dict[str, Any]] = []
    for index, citation in enumerate(citations, start=1):
        matching_reference = next(
            (passage for passage in retrieved if _citation_matches_reference(citation, passage)),
            None,
        )
        valid = matching_reference is not None
        valid_count += valid
        details.append(
            {
                "citation_index": index,
                "citation": citation,
                "provenance_valid": valid,
                "matching_retrieved_reference": (
                    {
                        "chunk_id": matching_reference.get("chunk_id"),
                        "source": matching_reference["source"],
                        "page": matching_reference.get("page"),
                        "section": matching_reference.get("section"),
                    }
                    if matching_reference is not None
                    else None
                ),
                "provenance_error": (
                    None if valid else "citation did not match a retrieved source/page/section"
                ),
            }
        )
    return valid_count, details


def _citation_matches_reference(
    citation: dict[str, Any],
    reference: dict[str, Any],
) -> bool:
    if citation.get("source") != reference.get("source"):
        return False
    citation_page = citation.get("page")
    reference_page = reference.get("page")
    if citation_page is None or reference_page is None or citation_page != reference_page:
        return False
    citation_section = citation.get("section")
    reference_section = reference.get("section")
    if citation_section and reference_section:
        if _normalize_section(citation_section) != _normalize_section(reference_section):
            return False
    return True


def citation_gold_relevance(
    citations: list[dict[str, Any]],
    gold_evidence: list[dict[str, Any]],
    retrieved: list[dict[str, Any]],
) -> tuple[int, list[dict[str, Any]]]:
    """Count citations that are provenance-valid and match expected gold evidence."""
    _, provenance_details = citation_provenance_validity(citations, retrieved)
    relevant_count = 0
    details: list[dict[str, Any]] = []
    for provenance in provenance_details:
        citation = provenance["citation"]
        matching_gold = next(
            (gold for gold in gold_evidence if _matches(citation, gold)),
            None,
        )
        relevant = provenance["provenance_valid"] and matching_gold is not None
        relevant_count += relevant
        details.append(
            {
                "citation_index": provenance["citation_index"],
                "citation": citation,
                "gold_evidence_relevant": relevant,
                "matching_gold_evidence": matching_gold,
                "relevance_error": (
                    None
                    if relevant
                    else (
                        provenance["provenance_error"]
                        if not provenance["provenance_valid"]
                        else "citation did not match expected gold evidence"
                    )
                ),
            }
        )
    return relevant_count, details


def _score_question(
    question: dict[str, Any],
    response: dict[str, Any] | None,
    error: str | None,
    elapsed_seconds: float,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        **question,
        "request_status": "failed" if error else "succeeded",
        "elapsed_seconds": round(elapsed_seconds, 2),
        "response": response,
        "request_error": error,
        "recall_at_3": None,
        "recall_at_5": None,
        "citation_provenance_validity_percentage": None,
        "gold_evidence_relevance_percentage": None,
        "citation_validity_percentage": None,
        "citation_details": [],
        "invalid_citations": [],
        "passed": False,
        "pass_reasons": [],
    }
    if response is None:
        row["pass_reasons"] = [f"request failed: {error}"]
        return row

    row["request_id"] = response["request_id"]
    row["response_metadata"] = {
        "confidence": response["confidence"],
        "provider": response["provider"],
        "answered_from_context": response["answered_from_context"],
        "retrieved_count": len(response["retrieved"]),
        "citation_count": len(response["citations"]),
    }
    row["recall_at_3"] = recall_at_k(question["gold_evidence"], response["retrieved"], 3)
    row["recall_at_5"] = recall_at_k(question["gold_evidence"], response["retrieved"], 5)
    if response["citations"]:
        provenance_valid, provenance_details = citation_provenance_validity(
            response["citations"],
            response["retrieved"],
        )
        gold_relevant, relevance_details = citation_gold_relevance(
            response["citations"], question["gold_evidence"], response["retrieved"]
        )
        citation_count = len(response["citations"])
        row["citation_provenance_validity_percentage"] = (
            100.0 * provenance_valid / citation_count
        )
        row["gold_evidence_relevance_percentage"] = 100.0 * gold_relevant / citation_count
        row["citation_validity_percentage"] = row["citation_provenance_validity_percentage"]
        row["citation_details"] = [
            {**provenance, **relevance}
            for provenance, relevance in zip(provenance_details, relevance_details)
        ]
        row["invalid_citations"] = [
            {
                "citation": detail["citation"],
                "reason": detail["relevance_error"],
            }
            for detail in relevance_details
            if not detail["gold_evidence_relevant"]
        ]
    else:
        row["invalid_citations"] = []

    reasons: list[str] = []
    if question["expected_answerability"] == "must_abstain":
        abstained = not response["answered_from_context"] and not response["citations"]
        row["abstention_correct"] = abstained
        if not abstained:
            if response["answered_from_context"]:
                reasons.append("must-abstain question was answered from context")
            if response["citations"]:
                reasons.append("must-abstain response included unsupported citations")
    else:
        row["answerable_answered_from_context"] = response["answered_from_context"]
        if not response["answered_from_context"]:
            row["answerable_incorrectly_abstained"] = True
            reasons.append("answerable question was incorrectly abstained on")
        if row["recall_at_5"] != 1.0:
            reasons.append("not all gold evidence was retrieved in the top 5")
        if not response["citations"]:
            reasons.append("answerable response contained no citations")
        elif row["gold_evidence_relevance_percentage"] != 100.0:
            reasons.append("one or more citations did not match gold evidence")

    row["passed"] = not reasons
    row["pass_reasons"] = reasons
    return row


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute complete-run metrics, retaining unavailable values as null."""
    successful = [row for row in rows if row["request_status"] == "succeeded"]
    failures = [row for row in rows if row["request_status"] == "failed"]
    answerable = [row for row in rows if row["expected_answerability"] == "answerable"]
    abstain = [row for row in rows if row["expected_answerability"] == "must_abstain"]

    def aggregate_recall(selected: list[dict[str, Any]], field: str) -> float | None:
        if not selected or any(row["request_status"] != "succeeded" for row in selected):
            return None
        denominator = sum(len(row["gold_evidence"]) for row in selected)
        if denominator == 0:
            return None
        retrieved_count = sum(
            round((row[field] or 0.0) * len(row["gold_evidence"]))
            for row in selected
        )
        return retrieved_count / denominator

    citation_details = [
        {"question_id": row["id"], **detail}
        for row in successful
        for detail in row["citation_details"]
    ]
    citation_count = len(citation_details)
    citation_provenance_valid_count = sum(
        detail["provenance_valid"] for detail in citation_details
    )
    gold_evidence_relevant_count = sum(
        detail["gold_evidence_relevant"] for detail in citation_details
    )

    per_category: dict[str, Any] = {}
    categories = sorted({row["category"] for row in rows})
    for category in categories:
        selected = [row for row in rows if row["category"] == category]
        per_category[category] = {
            "questions": len(selected),
            "successful_requests": sum(row["request_status"] == "succeeded" for row in selected),
            "recall_at_3": aggregate_recall(selected, "recall_at_3")
            if any(row["expected_answerability"] == "answerable" for row in selected)
            else None,
            "recall_at_5": aggregate_recall(selected, "recall_at_5")
            if any(row["expected_answerability"] == "answerable" for row in selected)
            else None,
            "must_abstain_correctness_percentage": _abstention_percentage(selected),
        }

    citation_provenance_percentage = (
        100.0 * citation_provenance_valid_count / citation_count
        if citation_count and not failures
        else None
    )
    gold_relevance_percentage = (
        100.0 * gold_evidence_relevant_count / citation_count
        if citation_count and not failures
        else None
    )
    return {
        "total_questions": len(rows),
        "successful_requests": len(successful),
        "request_failures": len(failures),
        "request_failure_details": [
            {"id": row["id"], "error": row["request_error"]} for row in failures
        ],
        "answerable_questions": len(answerable),
        "answerable_answered_from_context": sum(
            bool(row.get("response") and row["response"]["answered_from_context"])
            for row in answerable
        ),
        "answerable_incorrectly_abstained": sum(
            bool(row.get("answerable_incorrectly_abstained")) for row in answerable
        ),
        "recall_at_3": aggregate_recall(answerable, "recall_at_3"),
        "recall_at_5": aggregate_recall(answerable, "recall_at_5"),
        "citation_provenance_validity_percentage": citation_provenance_percentage,
        "gold_evidence_relevance_percentage": gold_relevance_percentage,
        "citation_validity_percentage": citation_provenance_percentage,
        "citation_count": citation_count,
        "valid_citation_count": citation_provenance_valid_count,
        "gold_evidence_relevant_citation_count": gold_evidence_relevant_count,
        "invalid_citation_provenance": [
            {
                "question_id": detail["question_id"],
                "citation": detail["citation"],
                "reason": detail["provenance_error"],
            }
            for detail in citation_details
            if not detail["provenance_valid"]
        ],
        "gold_irrelevant_citations": [
            {
                "question_id": detail["question_id"],
                "citation": detail["citation"],
                "reason": detail["relevance_error"],
            }
            for detail in citation_details
            if not detail["gold_evidence_relevant"]
        ],
        "invalid_citations": [
            {"question_id": row["id"], **invalid}
            for row in successful
            for invalid in row["invalid_citations"]
        ],
        "must_abstain_cases": [
            {
                "id": row["id"],
                "request_status": row["request_status"],
                "correct": row.get("abstention_correct"),
                "reason": row["request_error"]
                if row["request_status"] == "failed"
                else (None if row.get("abstention_correct") else "; ".join(row["pass_reasons"])),
            }
            for row in abstain
        ],
        "must_abstain_correctness_percentage": _abstention_percentage(abstain),
        "per_category": per_category,
        "passed_questions": sum(row["passed"] for row in rows),
        "failed_questions": sum(not row["passed"] for row in rows),
        "metrics_unavailable_reason": (
            "One or more requests failed; aggregate metrics requiring the complete dataset "
            "are unavailable."
            if failures
            else None
        ),
    }


def _abstention_percentage(rows: list[dict[str, Any]]) -> float | None:
    if not rows or any(row["request_status"] != "succeeded" for row in rows):
        return None
    correct = sum(bool(row.get("abstention_correct")) for row in rows)
    return 100.0 * correct / len(rows)


def gate(results: dict[str, Any], baseline: dict[str, Any] | None) -> list[str]:
    """Return evaluation gate failures, without treating unavailable metrics as zero."""
    summary = results["metrics"]
    failures: list[str] = []
    if summary["request_failures"]:
        failures.append(f"{summary['request_failures']} API request(s) failed")
    if summary["failed_questions"]:
        failures.append(f"{summary['failed_questions']} question(s) failed evaluation checks")
    if summary["must_abstain_correctness_percentage"] is None:
        failures.append("must-abstain correctness is unavailable")
    elif summary["must_abstain_correctness_percentage"] < REQUIRED_ABSTENTION_PERCENTAGE:
        failures.append("must-abstain correctness is below 100%")
    provenance_validity = summary["citation_provenance_validity_percentage"]
    if provenance_validity is None:
        failures.append("citation provenance validity is unavailable")
    elif provenance_validity < MIN_CITATION_VALIDITY_PERCENTAGE:
        failures.append("citation provenance validity is below 95%")
    if baseline is not None:
        baseline_recall = baseline.get("recall_at_5")
        current_recall = summary["recall_at_5"]
        if current_recall is None or not isinstance(baseline_recall, (int, float)):
            failures.append("Recall@5 baseline comparison is unavailable")
        elif current_recall < baseline_recall - MAX_RECALL5_DROP:
            failures.append("Recall@5 dropped more than 5 percentage points below baseline")
    return failures


def _safe_base_url(url: str) -> str:
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("API base URL has an invalid port") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("API base URL must be an HTTP(S) URL with a hostname")
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    if port is not None:
        host = f"{host}:{port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def _baseline_snapshot(results: dict[str, Any]) -> dict[str, Any]:
    metrics = results["metrics"]
    return {
        "evaluation_timestamp": results["evaluation_timestamp"],
        "api_base_url": results["api_base_url"],
        "number_of_evaluated_questions": metrics["total_questions"],
        "successful_requests": metrics["successful_requests"],
        "recall_at_3": metrics["recall_at_3"],
        "recall_at_5": metrics["recall_at_5"],
        "citation_validity_percentage": metrics["citation_provenance_validity_percentage"],
        "citation_provenance_validity_percentage": metrics[
            "citation_provenance_validity_percentage"
        ],
        "gold_evidence_relevance_percentage": metrics["gold_evidence_relevance_percentage"],
        "must_abstain_correctness_percentage": metrics[
            "must_abstain_correctness_percentage"
        ],
        "per_category_metrics": metrics["per_category"],
        "evaluation_gate_passed": results["gate"]["passed"],
        "evaluation_gate_failures": results["gate"]["failures"],
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _load_baseline(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        baseline = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read existing baseline {path}: {exc}") from exc
    if not isinstance(baseline, dict):
        raise ValueError(f"existing baseline {path} must contain a JSON object")
    return baseline


def _arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate RAHBAR against a live golden set.")
    parser.add_argument(
        "--api",
        default=os.getenv("RAHBAR_EVAL_BASE_URL", DEFAULT_BASE_URL),
        help="API base URL (default: RAHBAR_EVAL_BASE_URL or http://localhost:8080)",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("RAHBAR_EVAL_HOST", DEFAULT_HOST),
        help="Ingress Host header (default: RAHBAR_EVAL_HOST or rahbar.local)",
    )
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN, help="golden YAML path")
    parser.add_argument("--out", type=Path, default=HERE / "results.json", help="results JSON path")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=HERE / "baseline.json",
        help="baseline JSON path (existing baseline is never overwritten)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=REQUEST_TIMEOUT_SECONDS,
        help="per-request timeout in seconds",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _arg_parser().parse_args(argv)
    if args.timeout <= 0:
        print("error: --timeout must be positive", file=sys.stderr)
        return 2
    try:
        safe_api_base_url = _safe_base_url(args.api)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    require_assignment_counts = args.golden.resolve() == DEFAULT_GOLDEN.resolve()
    try:
        questions = load_golden(args.golden, require_assignment_counts=require_assignment_counts)
        baseline = _load_baseline(args.baseline)
    except (GoldenSetError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    headers = {"Host": args.host} if args.host else {}
    rows: list[dict[str, Any]] = []
    try:
        with httpx.Client(timeout=args.timeout, headers=headers) as client:
            for question in questions:
                started = time.perf_counter()
                try:
                    response = ask(client, args.api, question["question"])
                    error = None
                except EvaluationRequestError as exc:
                    response = None
                    error = str(exc)
                elapsed = time.perf_counter() - started
                row = _score_question(question, response, error, elapsed)
                rows.append(row)
                request_id = row.get("request_id", "FAILED")
                print(
                    f"{question['id']:<6} {request_id:<24} "
                    f"{elapsed:>6.2f}s  {'PASS' if row['passed'] else 'FAIL'}  "
                    f"{question['question'][:60]}"
                )
    except httpx.HTTPError as exc:
        print(f"error: unable to initialize API client: {exc}", file=sys.stderr)
        return 2

    timestamp = datetime.now(timezone.utc).isoformat()
    results: dict[str, Any] = {
        "evaluation_timestamp": timestamp,
        "api_base_url": safe_api_base_url,
        "golden_file": args.golden.name,
        "metrics": summarise(rows),
        "questions": rows,
    }
    failures = gate(results, baseline)
    results["gate"] = {
        "passed": not failures,
        "failures": failures,
        "baseline_comparison_available": baseline is not None,
    }
    try:
        _write_json(args.out, results)
    except OSError as exc:
        print(f"error: cannot write results to {args.out}: {exc}", file=sys.stderr)
        return 2

    metrics = results["metrics"]
    print("\nEvaluation summary")
    print(f"  Questions: {metrics['total_questions']}")
    print(f"  Successful requests: {metrics['successful_requests']}")
    print(f"  Request failures: {metrics['request_failures']}")
    print(f"  Recall@3: {_format_metric(metrics['recall_at_3'])}")
    print(f"  Recall@5: {_format_metric(metrics['recall_at_5'])}")
    print(
        "  Citation provenance validity: "
        f"{_format_percentage(metrics['citation_provenance_validity_percentage'])}"
    )
    print(
        "  Gold-evidence relevance: "
        f"{_format_percentage(metrics['gold_evidence_relevance_percentage'])}"
    )
    print(
        "  Must-abstain correctness: "
        f"{_format_percentage(metrics['must_abstain_correctness_percentage'])}"
    )
    print(f"  Results written to {args.out}")

    if metrics["request_failures"] == 0 and not args.baseline.exists():
        try:
            _write_json(args.baseline, _baseline_snapshot(results))
            print(f"  Baseline snapshot created at {args.baseline}")
        except OSError as exc:
            print(f"error: cannot write baseline to {args.baseline}: {exc}", file=sys.stderr)
            return 2
    elif args.baseline.exists():
        print(f"  Existing baseline preserved at {args.baseline}")

    if failures:
        print("\nGATE FAILED")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nGate passed")
    return 0


def _format_metric(value: float | None) -> str:
    return "unavailable" if value is None else f"{100 * value:.1f}%"


def _format_percentage(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.1f}%"


if __name__ == "__main__":
    sys.exit(main())
