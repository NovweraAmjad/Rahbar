from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from eval import run_eval


def _response(
    *,
    answered_from_context: bool = True,
    citations: list[dict[str, Any]] | None = None,
    retrieved: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "request_id": "req-test",
        "answer": "A grounded answer.",
        "citations": citations or [],
        "answered_from_context": answered_from_context,
        "confidence": "high",
        "retrieved": retrieved or [],
        "provider": "stub",
    }


def _evidence(
    source: str = "report.pdf",
    page: int = 13,
    section: str = "Faculty Summary",
) -> dict[str, Any]:
    return {"source": source, "page": page, "section": section}


def test_loads_golden_with_required_category_counts() -> None:
    questions = run_eval.load_golden(
        run_eval.DEFAULT_GOLDEN,
        require_assignment_counts=True,
    )

    assert len(questions) == 15
    counts = {category: sum(q["category"] == category for q in questions) for category in run_eval.CATEGORIES}
    assert counts == run_eval.CATEGORY_COUNTS
    assert questions[12]["question"] == "What food is served in the FAST-NUCES cafeteria?"
    assert questions[14]["category"] == "prompt-injection"


@pytest.mark.parametrize(
    ("raw_question", "message"),
    [
        (
            {
                "id": "missing-evidence",
                "category": "ordinary",
                "question": "Where is the answer?",
                "expected_answerability": "answerable",
            },
            "require gold_evidence",
        ),
        (
            {
                "id": "invalid-page",
                "category": "ordinary",
                "question": "Where is the answer?",
                "expected_answerability": "answerable",
                "gold_evidence": [{"source": "doc.pdf", "page": 0}],
            },
            "invalid page",
        ),
        (
            {
                "id": "missing-source",
                "category": "ordinary",
                "question": "Where is the answer?",
                "expected_answerability": "answerable",
                "gold_evidence": [{"page": 1}],
            },
            "source filename",
        ),
    ],
)
def test_rejects_invalid_or_missing_gold_evidence(
    raw_question: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(run_eval.GoldenSetError, match=message):
        run_eval.validate_questions([raw_question])


def test_rejects_gold_evidence_source_not_in_manifest() -> None:
    question = {
        "id": "unknown-source",
        "category": "ordinary",
        "question": "Where is the answer?",
        "expected_answerability": "answerable",
        "gold_evidence": [{"source": "not-in-corpus.pdf", "page": 1}],
    }

    with pytest.raises(run_eval.GoldenSetError, match="not listed in corpus/MANIFEST.md"):
        run_eval.validate_questions([question], allowed_sources={"known.pdf"})


def test_retrieval_matching_requires_exact_source_and_gold_page() -> None:
    evidence = [_evidence()]
    wrong_page = [{"source": "report.pdf", "page": 12, "section": "Faculty Summary"}]
    wrong_source = [{"source": "other.pdf", "page": 13, "section": "Faculty Summary"}]

    assert run_eval.recall_at_k(evidence, wrong_page, 5) == 0.0
    assert run_eval.recall_at_k(evidence, wrong_source, 5) == 0.0
    assert run_eval.recall_at_k(evidence, [evidence[0]], 5) == 1.0


def test_retrieval_does_not_require_section_when_gold_has_no_section() -> None:
    evidence = [{"source": "report.pdf", "page": 13}]
    retrieved = [_evidence()]

    assert run_eval.recall_at_k(evidence, retrieved, 5) == 1.0


def test_recall_at_3_and_recall_at_5_use_ranked_results() -> None:
    gold = [_evidence()]
    irrelevant = [
        {"source": f"other-{index}.pdf", "page": 1, "section": "Other"}
        for index in range(4)
    ]
    retrieved = irrelevant + [gold[0]]

    assert run_eval.recall_at_k(gold, retrieved, 3) == 0.0
    assert run_eval.recall_at_k(gold, retrieved, 5) == 1.0
    assert run_eval.recall_at_k([], retrieved, 5) is None


def test_citation_provenance_requires_source_page_and_available_section() -> None:
    retrieved = [_evidence()]
    valid, details = run_eval.citation_provenance_validity([_evidence()], retrieved)
    assert valid == 1
    assert details[0]["provenance_valid"] is True
    assert details[0]["matching_retrieved_reference"]["page"] == 13

    wrong_page = {"source": "report.pdf", "page": 12, "section": "Faculty Summary"}
    valid, details = run_eval.citation_provenance_validity([wrong_page], retrieved)
    assert valid == 0
    assert details[0]["provenance_valid"] is False

    wrong_section = {"source": "report.pdf", "page": 13, "section": "Other"}
    valid, details = run_eval.citation_provenance_validity([wrong_section], retrieved)
    assert valid == 0
    assert details[0]["provenance_valid"] is False

    wrong_source = {"source": "different.pdf", "page": 13, "section": "Faculty Summary"}
    valid, details = run_eval.citation_provenance_validity([wrong_source], retrieved)
    assert valid == 0
    assert details[0]["provenance_valid"] is False

    unavailable_section_citation = {"source": "report.pdf", "page": 13}
    unavailable_section_ref = {"source": "report.pdf", "page": 13}
    valid, details = run_eval.citation_provenance_validity(
        [unavailable_section_citation],
        [unavailable_section_ref],
    )
    assert valid == 1
    assert details[0]["provenance_valid"] is True

    missing_page = {"source": "report.pdf", "section": "Faculty Summary"}
    valid, details = run_eval.citation_provenance_validity([missing_page], retrieved)
    assert valid == 0
    assert details[0]["provenance_valid"] is False


def test_gold_evidence_relevance_remains_strict() -> None:
    citation = {"source": "report.pdf", "page": 13, "section": "Faculty Summary"}
    retrieved = [citation]
    wrong_gold = [{"source": "other.pdf", "page": 13, "section": "Faculty Summary"}]

    valid, _ = run_eval.citation_provenance_validity([citation], retrieved)
    relevant, details = run_eval.citation_gold_relevance([citation], wrong_gold, retrieved)

    assert valid == 1
    assert relevant == 0
    assert details[0]["gold_evidence_relevant"] is False


def test_q03_and_q11_style_citations_separate_provenance_from_relevance() -> None:
    q03_gold = {
        "id": "q03",
        "category": "ordinary",
        "question": "What is the attendance consequence?",
        "expected_answerability": "answerable",
        "gold_evidence": [
            {
                "source": "academic-rules-handbook-jun-2026.pdf",
                "page": 10,
                "section": "Attendance Requirements",
            }
        ],
    }
    q03_retrieved = [
        {
            "chunk_id": f"chunk-{index}",
            "source": "academic-rules-handbook-jun-2026.pdf",
            "page": page,
            "section": section,
            "score": 0.8,
        }
        for index, (page, section) in enumerate(
            [
                (10, "Attendance Requirements"),
                (11, "Attendance Requirements 2. Grading and GPA Calculations"),
                (11, "A, A- Excellent"),
                (11, "A, A- Excellent"),
                (11, "A, A- Excellent"),
                (28, "Page 28 (no section heading detected)"),
                (29, "Page 29 (no section heading detected)"),
            ],
            start=1,
        )
    ]
    q03_response = _response(
        citations=[
            {key: value for key, value in ref.items() if key in {"source", "page", "section"}}
            for ref in q03_retrieved
        ],
        retrieved=q03_retrieved,
    )
    q11_gold = {
        "id": "q11",
        "category": "exact-term",
        "question": "Which clause contains attendance requirements?",
        "expected_answerability": "answerable",
        "gold_evidence": [
            {
                "source": "academic-rules-handbook-jun-2026.pdf",
                "page": 10,
                "section": "Attendance Requirements",
            }
        ],
    }
    prospectus_ref = {
        "chunk_id": "prospectus-attendance",
        "source": "prospectus-2026-academic-rules-and-regulations.pdf",
        "page": 1,
        "section": "Page 1 (no section heading detected)",
        "score": 0.7,
    }
    q11_response = _response(
        citations=[
            {
                "source": prospectus_ref["source"],
                "page": prospectus_ref["page"],
                "section": prospectus_ref["section"],
            }
        ],
        retrieved=[prospectus_ref],
    )

    q03_row = run_eval._score_question(q03_gold, q03_response, None, 0.1)
    q11_row = run_eval._score_question(q11_gold, q11_response, None, 0.1)
    metrics = run_eval.summarise([q03_row, q11_row])

    assert q03_row["citation_provenance_validity_percentage"] == 100.0
    assert q03_row["gold_evidence_relevance_percentage"] == pytest.approx(100 / 7)
    assert q03_row["citation_details"][0]["gold_evidence_relevant"] is True
    assert q03_row["citation_details"][1]["provenance_valid"] is True
    assert q03_row["citation_details"][1]["gold_evidence_relevant"] is False
    assert q11_row["citation_provenance_validity_percentage"] == 100.0
    assert q11_row["gold_evidence_relevance_percentage"] == 0.0
    assert metrics["citation_provenance_validity_percentage"] == 100.0
    assert metrics["gold_evidence_relevance_percentage"] == pytest.approx(100 / 8)


def test_must_abstain_correctness_requires_no_context_answer_or_citations() -> None:
    question = {
        "id": "abstain",
        "category": "must-abstain",
        "question": "What is not in the corpus?",
        "expected_answerability": "must_abstain",
        "gold_evidence": [],
    }
    correct = run_eval._score_question(
        question,
        _response(answered_from_context=False),
        None,
        0.1,
    )
    incorrect = run_eval._score_question(
        question,
        _response(answered_from_context=False, citations=[_evidence()]),
        None,
        0.1,
    )

    assert correct["abstention_correct"] is True
    assert correct["passed"] is True
    assert incorrect["abstention_correct"] is False
    assert incorrect["passed"] is False


def test_ask_reports_transport_failure_without_exposing_request_url() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    client = httpx.Client(transport=httpx.MockTransport(fail))
    with client, pytest.raises(run_eval.EvaluationRequestError, match="API request failed"):
        run_eval.ask(client, "http://example.test", "Question?")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("not-json", "malformed JSON"),
        ('{"request_id": "req"}', "string answer"),
        (
            json.dumps(
                {
                    **_response(),
                    "retrieved": [
                        {
                            "chunk_id": "c1",
                            "source": "doc.pdf",
                            "section": "Section",
                            "page": 1,
                            "score": "not-a-number",
                        }
                    ],
                }
            ),
            "invalid score",
        ),
    ],
)
def test_ask_rejects_malformed_api_responses(body: str, message: str) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=body, request=request)
        )
    )
    with client, pytest.raises(run_eval.EvaluationRequestError, match=message):
        run_eval.ask(client, "http://example.test", "Question?")


def test_results_and_baseline_json_serialize(tmp_path: Path) -> None:
    evidence = [_evidence()]
    response = _response(
        citations=[_evidence()],
        retrieved=[_evidence()],
    )
    question = {
        "id": "answerable",
        "category": "ordinary",
        "question": "Where is the answer?",
        "expected_answerability": "answerable",
        "gold_evidence": evidence,
    }
    row = run_eval._score_question(question, response, None, 0.2)
    metrics = run_eval.summarise([row])
    results = {
        "evaluation_timestamp": "2026-11-07T00:00:00+00:00",
        "api_base_url": "http://localhost:8080",
        "metrics": metrics,
        "questions": [row],
        "gate": {"passed": False, "failures": ["test failure"]},
    }
    results_path = tmp_path / "results.json"
    baseline_path = tmp_path / "baseline.json"

    run_eval._write_json(results_path, results)
    baseline = run_eval._baseline_snapshot(results)
    run_eval._write_json(baseline_path, baseline)

    assert json.loads(results_path.read_text(encoding="utf-8"))["questions"][0]["request_id"] == "req-test"
    assert json.loads(baseline_path.read_text(encoding="utf-8"))["number_of_evaluated_questions"] == 1


def test_safe_base_url_removes_credentials_and_query_values() -> None:
    assert (
        run_eval._safe_base_url("https://user:secret@example.test:8443/rag?token=private")
        == "https://example.test:8443/rag"
    )


def test_safe_base_url_rejects_non_http_urls() -> None:
    with pytest.raises(ValueError, match="HTTP"):
        run_eval._safe_base_url("ftp://example.test")
