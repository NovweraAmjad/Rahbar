from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from eval import golden_schema


HERE = Path(__file__).resolve().parent
GOLDEN_JSONL = HERE / "golden.jsonl"
PRE_PART_E_GOLDEN_JSONL = HERE / "golden.pre-part-e.jsonl"


def test_golden_jsonl_assignment_counts_and_review_labels() -> None:
    questions = golden_schema.load_golden_jsonl(GOLDEN_JSONL)

    assert len(questions) >= 36
    answerable = sum(
        question["expected_answerability"] == "answerable"
        for question in questions
    )
    unanswerable = sum(
        question["expected_answerability"] == "must_abstain"
        for question in questions
    )
    hard = sum(question["category"] == "hard" for question in questions)
    human = sum(question["origin"] == "human" for question in questions)
    reviewed_synthetic = sum(
        question["origin"] == "synthetic"
        and question["manual_review"]["status"] == "reviewed"
        for question in questions
    )

    assert answerable >= 15
    assert unanswerable >= 5
    assert hard >= 5
    assert human >= 8
    assert reviewed_synthetic >= 8


def test_pre_part_e_golden_snapshot_preserves_original_30_questions() -> None:
    questions = golden_schema.load_golden_jsonl(PRE_PART_E_GOLDEN_JSONL)

    assert len(questions) == 30
    assert hashlib.sha256(PRE_PART_E_GOLDEN_JSONL.read_bytes()).hexdigest() == (
        "0dae3da1fcb30c64e650a066fe350458d6787860b4cab867f7eb666e2ba73f7f"
    )
    assert [
        line
        for line in GOLDEN_JSONL.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ][:30] == [
        line
        for line in PRE_PART_E_GOLDEN_JSONL.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda q: q.pop("origin"),
        lambda q: q.pop("manual_review"),
        lambda q: q.update(expected_answer=None),
        lambda q: q.update(category="not-a-category"),
        lambda q: q.update(gold_evidence=[]),
        lambda q: q.update(expected_answerability="maybe"),
    ],
)
def test_golden_schema_rejects_missing_or_invalid_fields(mutation) -> None:
    questions = json.loads(
        "\n".join(
            line
            for line in GOLDEN_JSONL.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ).replace("\n", ",")
    ) if False else [
        json.loads(line)
        for line in GOLDEN_JSONL.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    answerable = next(
        question
        for question in questions
        if question["expected_answerability"] == "answerable"
    )
    mutation(answerable)

    with pytest.raises(golden_schema.GoldenSchemaError):
        golden_schema.validate_golden(questions)


def test_validated_answers_exist_at_their_gold_pages() -> None:
    questions = golden_schema.load_golden_jsonl(GOLDEN_JSONL)

    try:
        import pdfplumber
    except ImportError:
        pytest.skip("pdfplumber is unavailable")

    corpus = HERE.parent / "corpus"
    raw_dir = corpus / "raw"
    if not raw_dir.is_dir() or not any(raw_dir.glob("*.pdf")):
        pytest.skip("raw PDF corpus is not present (gitignored; run corpus/fetch.py)")
    page_checks = {
        "q01": ("academic-rules-handbook-jun-2026.pdf", 10, ["100%", "20%"]),
        "q09": ("annual-report-2019-20.pdf", 13, ["615"]),
        "q10": ("annual-report-2023-24.pdf", 13, ["735"]),
        "q12": ("student-handbook-jun-2026.pdf", 48, ["Generative AI"]),
        "q21": ("academic-rules-handbook-jun-2026.pdf", 12, ["4.00", "0.00"]),
        "q25": ("annual-report-2023-24.pdf", 13, ["735"]),
    }

    for question in questions:
        evidence = question["gold_evidence"]
        if not evidence:
            continue
        for item in evidence:
            source = item["source"]
            page = item["page"]
            if source.endswith(".txt"):
                text = (
                    corpus / "batch-b" / source
                ).read_text(encoding="utf-8")
                assert text.strip()
            else:
                with pdfplumber.open(corpus / "raw" / source) as pdf:
                    assert 1 <= page <= len(pdf.pages)

    for question_id, (source, page, terms) in page_checks.items():
        with pdfplumber.open(corpus / "raw" / source) as pdf:
            text = pdf.pages[page - 1].extract_text() or ""
        assert all(term.casefold() in text.casefold() for term in terms)
