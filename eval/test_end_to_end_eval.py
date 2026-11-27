from __future__ import annotations


import pytest

from eval import run_end_to_end_eval as end_to_end


def _row(
    *,
    answerable: bool = True,
    status: str = "succeeded",
    support: str = "faithful",
    abstention: bool | None = None,
    citations: int = 1,
    valid: int = 1,
    latency: float = 1.0,
) -> dict:
    return {
        "request_status": status,
        "expected_answerability": "answerable" if answerable else "must_abstain",
        "context_support_status": support,
        "abstention_correct": abstention,
        "citation_count": citations,
        "valid_citation_count": valid,
        "latency_seconds": latency,
    }


def test_percentile_known_values() -> None:
    assert end_to_end.percentile([1.0, 2.0, 3.0], 50.0) == pytest.approx(2.0)
    assert end_to_end.percentile([1.0, 2.0, 3.0], 95.0) == pytest.approx(2.9)


def test_citation_validity_requires_retrieved_source_page_and_section() -> None:
    retrieved = [{
        "source": "rules.pdf",
        "page": 10,
        "section": "Attendance Requirements",
    }]
    assert end_to_end.citation_is_valid(
        {"source": "rules.pdf", "page": 10, "section": "Attendance Requirements"},
        retrieved,
    )
    assert not end_to_end.citation_is_valid(
        {"source": "rules.pdf", "page": 11, "section": "Attendance Requirements"},
        retrieved,
    )


def test_context_support_status_is_conservative() -> None:
    retrieved = [{
        "source": "rules.pdf",
        "page": 10,
        "section": "Attendance Requirements",
    }]
    faithful = {
        "answered_from_context": True,
        "citations": [{
            "source": "rules.pdf",
            "page": 10,
            "section": "Attendance Requirements",
        }],
        "retrieved": retrieved,
    }
    unsupported = {
        "answered_from_context": True,
        "citations": [{
            "source": "rules.pdf",
            "page": 99,
            "section": "Wrong page",
        }],
        "retrieved": retrieved,
    }
    assert end_to_end.context_support_status(faithful) == "faithful"
    assert end_to_end.context_support_status(unsupported) == "unsupported"
    assert end_to_end.context_support_status({
        "answered_from_context": False,
        "citations": [],
        "retrieved": retrieved,
    }) == "not_answered"


def test_abstention_status() -> None:
    unanswerable = {"expected_answerability": "must_abstain"}
    assert end_to_end.abstention_status(unanswerable, {
        "answered_from_context": False,
        "citations": [],
    })
    assert not end_to_end.abstention_status(unanswerable, {
        "answered_from_context": True,
        "citations": [],
    })


def test_aggregate_end_to_end_metrics_separated() -> None:
    rows = [
        _row(answerable=True, support="faithful", citations=2, valid=2, latency=1.0),
        _row(answerable=True, support="unsupported", citations=1, valid=0, latency=3.0),
        _row(answerable=False, abstention=True, citations=0, valid=0, latency=2.0),
        _row(answerable=False, abstention=False, citations=0, valid=0, latency=4.0),
    ]
    metrics = end_to_end.aggregate_end_to_end(rows)

    assert metrics["faithfulness"]["percentage"] == pytest.approx(50.0)
    assert metrics["abstention_correctness"]["percentage"] == pytest.approx(50.0)
    assert metrics["citation_validity"]["percentage"] == pytest.approx(66.66666666666667)
    assert metrics["latency"]["p50_seconds"] == pytest.approx(2.5)
    assert metrics["latency"]["p95_seconds"] == pytest.approx(3.85)


def test_request_failures_make_accuracy_metrics_unavailable() -> None:
    rows = [
        _row(answerable=True, status="failed", support="request_failed"),
        _row(answerable=True, support="faithful"),
        _row(answerable=False, abstention=True),
    ]
    metrics = end_to_end.aggregate_end_to_end(rows)
    assert metrics["faithfulness"]["percentage"] is None
    assert metrics["abstention_correctness"]["percentage"] == pytest.approx(100.0)
    assert metrics["citation_validity"]["percentage"] == pytest.approx(100.0)


def test_output_validation_rejects_missing_sections() -> None:
    with pytest.raises(end_to_end.EndToEndError, match="missing required fields"):
        end_to_end.validate_output({"metrics": {}, "questions": []})
