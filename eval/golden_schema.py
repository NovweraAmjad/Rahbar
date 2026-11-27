"""Schema and count validation for eval/golden.jsonl."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REQUIRED_TOTAL = 25
MIN_ANSWERABLE = 15
MIN_UNANSWERABLE = 5
MIN_HARD = 5
MIN_HUMAN = 8
MIN_REVIEWED_SYNTHETIC = 8
VALID_CATEGORIES = {
    "ordinary",
    "version-sensitive",
    "exact-term",
    "must-abstain",
    "prompt-injection",
    "hard",
}


class GoldenSchemaError(ValueError):
    pass


def load_golden_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise GoldenSchemaError(f"missing golden dataset: {path}")
    questions: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GoldenSchemaError(f"line {line_number}: invalid JSON: {exc}") from exc
        if not isinstance(item, dict):
            raise GoldenSchemaError(f"line {line_number}: question must be a JSON object")
        questions.append(item)
    return validate_golden(questions)


def validate_golden(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(questions) < REQUIRED_TOTAL:
        raise GoldenSchemaError(
            f"golden dataset requires at least {REQUIRED_TOTAL} questions; got {len(questions)}"
        )

    ids: set[str] = set()
    answerable = 0
    unanswerable = 0
    hard = 0
    human = 0
    reviewed_synthetic = 0

    for index, question in enumerate(questions, 1):
        prefix = f"question {index}"
        question_id = question.get("id")
        if not isinstance(question_id, str) or not question_id.strip():
            raise GoldenSchemaError(f"{prefix}: missing non-empty id")
        if question_id in ids:
            raise GoldenSchemaError(f"{prefix}: duplicate id {question_id}")
        ids.add(question_id)

        category = question.get("category")
        if category not in VALID_CATEGORIES:
            raise GoldenSchemaError(f"{question_id}: invalid category {category!r}")

        text = question.get("question")
        if not isinstance(text, str) or not 3 <= len(text) <= 500:
            raise GoldenSchemaError(f"{question_id}: invalid question text")

        origin = question.get("origin")
        if origin not in {"human", "synthetic"}:
            raise GoldenSchemaError(f"{question_id}: missing or invalid origin label")
        if origin == "human":
            human += 1

        review = question.get("manual_review")
        if not isinstance(review, dict):
            raise GoldenSchemaError(f"{question_id}: missing manual_review")
        if not isinstance(review.get("status"), str) or not review["status"].strip():
            raise GoldenSchemaError(f"{question_id}: missing manual review status")
        if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
            raise GoldenSchemaError(f"{question_id}: missing manual review reviewer")
        if origin == "synthetic" and review["status"] == "reviewed":
            reviewed_synthetic += 1

        answerability = question.get("expected_answerability")
        if answerability not in {"answerable", "must_abstain"}:
            raise GoldenSchemaError(
                f"{question_id}: invalid expected_answerability {answerability!r}"
            )

        evidence = question.get("gold_evidence")
        if not isinstance(evidence, list):
            raise GoldenSchemaError(f"{question_id}: gold_evidence must be a list")

        if answerability == "answerable":
            answerable += 1
            expected_answer = question.get("expected_answer")
            if not isinstance(expected_answer, str) or not expected_answer.strip():
                raise GoldenSchemaError(
                    f"{question_id}: answerable question has no valid expected_answer"
                )
            if not evidence:
                raise GoldenSchemaError(
                    f"{question_id}: answerable question requires gold_evidence"
                )
        else:
            unanswerable += 1
            if evidence:
                raise GoldenSchemaError(
                    f"{question_id}: must-abstain question cannot have gold_evidence"
                )
            if question.get("expected_answer") not in (None, ""):
                raise GoldenSchemaError(
                    f"{question_id}: must-abstain question cannot have an answer"
                )

        if category in {"must-abstain", "prompt-injection"} and answerability != "must_abstain":
            raise GoldenSchemaError(
                f"{question_id}: category {category} requires abstention"
            )
        if category == "hard":
            hard += 1
            if question.get("hard_type") not in {
                "paraphrased",
                "multi_chunk",
                "batch_b",
                "injection",
            }:
                raise GoldenSchemaError(f"{question_id}: hard question has invalid hard_type")

        for evidence_index, item in enumerate(evidence, 1):
            if not isinstance(item, dict):
                raise GoldenSchemaError(
                    f"{question_id}: evidence {evidence_index} must be an object"
                )
            source = item.get("source")
            if not isinstance(source, str) or not source.strip():
                raise GoldenSchemaError(
                    f"{question_id}: evidence {evidence_index} missing source"
                )
            page = item.get("page")
            if isinstance(page, bool) or not isinstance(page, int) or page < 1:
                raise GoldenSchemaError(
                    f"{question_id}: evidence {evidence_index} has invalid page"
                )
            document_version = item.get("document_version")
            if not isinstance(document_version, str) or not document_version.strip():
                raise GoldenSchemaError(
                    f"{question_id}: evidence {evidence_index} missing document_version"
                )

        if category == "version-sensitive":
            if not question.get("expected_document") or not question.get("expected_version"):
                raise GoldenSchemaError(
                    f"{question_id}: version-sensitive question needs document and version"
                )

    if answerable < MIN_ANSWERABLE:
        raise GoldenSchemaError(f"need at least {MIN_ANSWERABLE} answerable questions")
    if unanswerable < MIN_UNANSWERABLE:
        raise GoldenSchemaError(f"need at least {MIN_UNANSWERABLE} unanswerable questions")
    if hard < MIN_HARD:
        raise GoldenSchemaError(f"need at least {MIN_HARD} hard questions")
    if human < MIN_HUMAN:
        raise GoldenSchemaError(f"need at least {MIN_HUMAN} human-written questions")
    if reviewed_synthetic < MIN_REVIEWED_SYNTHETIC:
        raise GoldenSchemaError(
            f"need at least {MIN_REVIEWED_SYNTHETIC} reviewed synthetic questions"
        )
    return questions


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Validate the RAHBAR golden JSONL")
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path(__file__).with_name("golden.jsonl"),
    )
    args = parser.parse_args()
    questions = load_golden_jsonl(args.path)
    print(
        json.dumps(
            {
                "path": str(args.path),
                "questions": len(questions),
                "answerable": sum(
                    question["expected_answerability"] == "answerable"
                    for question in questions
                ),
                "unanswerable": sum(
                    question["expected_answerability"] == "must_abstain"
                    for question in questions
                ),
                "hard": sum(question["category"] == "hard" for question in questions),
                "human": sum(question["origin"] == "human" for question in questions),
                "reviewed_synthetic": sum(
                    question["origin"] == "synthetic"
                    and question["manual_review"]["status"] == "reviewed"
                    for question in questions
                ),
            },
            indent=2,
        )
    )

