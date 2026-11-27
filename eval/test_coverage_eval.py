from __future__ import annotations

from pathlib import Path

from eval import run_coverage_eval


HERE = Path(__file__).resolve().parent


def test_live_query_log_has_assignment_counts_and_unique_labels() -> None:
    queries = run_coverage_eval.load_live_queries(HERE / "live_queries.yaml")

    assert len(queries) >= 40
    assert sum(query["golden_domain"] for query in queries) >= 28
    assert sum(not query["golden_domain"] for query in queries) >= 12
    assert len({query["topic"] for query in queries}) >= 20


def test_coverage_matching_and_topic_grouping() -> None:
    live = [
        {
            "id": "live-a",
            "topic": "covered-topic",
            "query": "covered",
            "golden_domain": True,
        },
        {
            "id": "live-b",
            "topic": "uncovered-topic",
            "query": "missing",
            "golden_domain": False,
        },
    ]
    golden = [
        {"id": "g1", "question": "near", "category": "ordinary"},
        {"id": "g2", "question": "other", "category": "must-abstain"},
    ]
    rows = run_coverage_eval.evaluate_matches(
        live,
        golden,
        {"live-a": [1.0, 0.0], "live-b": [1.0, -1.0]},
        {"g1": [1.0, 0.0], "g2": [0.0, 1.0]},
        0.85,
    )

    assert rows[0]["covered"] is True
    assert rows[0]["nearest_golden_id"] == "g1"
    assert rows[1]["nearest_golden_id"] == "g1"
    assert set(run_coverage_eval.group_uncovered(rows)) == {"uncovered-topic"}


def test_coverage_gate_uses_configured_minimum() -> None:
    assert run_coverage_eval.gate_exit_code(85.0, 0.85) == 0
    assert run_coverage_eval.gate_exit_code(84.999, 0.85) == 1
