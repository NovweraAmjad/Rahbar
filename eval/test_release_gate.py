from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from eval import run_release_gate as gate


def part_b_summary(
    *,
    recall: float = 0.64,
    faithfulness: float = 79.0,
    p95: float = 4.0,
    abstention: float = 8.0,
) -> dict:
    def run() -> dict:
        return {
            "module": {
                "mean_paraphrase_cosine": 0.8,
                "mean_unrelated_cosine": 0.3,
                "paraphrase_minus_unrelated": 0.5,
                "embedding_time_mean_seconds": 0.2,
                "embedding_time_p95_seconds": 0.4,
                "largest_determinism_difference": 0.0,
                "embedding_dimension": 768,
            },
            "component": {
                "baseline_recall_at_3": recall,
                "baseline_recall_at_5": recall,
                "baseline_mrr": 0.7,
                "baseline_ndcg_at_5": 0.8,
                "typo_recall_at_3": 0.7,
                "typo_recall_at_5": 0.8,
                "typo_mrr": 0.6,
                "typo_ndcg_at_5": 0.7,
            },
            "end_to_end": {
                "faithfulness_percentage": faithfulness,
                "abstention_correctness_percentage": abstention,
                "latency_p95_seconds": p95,
                "latency_p50_seconds": 1.0,
                "citation_validity_percentage": 100.0,
                "successful_requests": 30,
                "request_failures": 0,
            },
        }

    return {"runs": {"1": run(), "2": run()}}


def thresholds() -> dict:
    return {
        "max_recall_at_5_regression_percentage_points": 2.0,
        "min_faithfulness_percentage": 70.0,
        "max_p95_latency_seconds": 5.0,
        "abstention_not_lower_than_baseline": True,
    }


def test_gate_passes_all_checks() -> None:
    baseline = gate.extract_metrics(part_b_summary(), "baseline")
    candidate = gate.extract_metrics(
        part_b_summary(
            recall=0.63,
            faithfulness=75.0,
            p95=4.5,
            abstention=10.0,
        ),
        "candidate",
    )
    result = gate.evaluate_gate(baseline, candidate, thresholds())

    assert result["passed"] is True
    assert result["failed_checks"] == []


@pytest.mark.parametrize(
    ("metric", "candidate_values"),
    [
        ("recall_at_5_regression", {"recall": 0.61}),
        ("faithfulness", {"faithfulness": 69.0}),
        ("p95_latency", {"p95": 5.1}),
        ("abstention_correctness", {"abstention": 7.0}),
    ],
)
def test_each_threshold_can_fail(metric: str, candidate_values: dict) -> None:
    baseline = gate.extract_metrics(part_b_summary(), "baseline")
    candidate = gate.extract_metrics(
        part_b_summary(**candidate_values),
        "candidate",
    )
    result = gate.evaluate_gate(baseline, candidate, thresholds())

    assert result["passed"] is False
    assert metric in result["failed_checks"]


def test_missing_metric_is_rejected() -> None:
    payload = part_b_summary()
    del payload["runs"]["2"]["end_to_end"]["latency_p95_seconds"]

    with pytest.raises(gate.ReleaseGateError, match="metric unavailable"):
        gate.extract_metrics(payload, "candidate")


def test_incomplete_end_to_end_result_is_rejected() -> None:
    payload = part_b_summary()
    payload["runs"]["2"]["end_to_end"]["request_failures"] = 1

    with pytest.raises(gate.ReleaseGateError, match="incomplete end-to-end requests"):
        gate.extract_metrics(payload, "candidate")


@pytest.mark.parametrize(
    ("level", "metric", "value", "message"),
    [
        ("component", "baseline_recall_at_5", 1.1, "above 1.0"),
        ("end_to_end", "faithfulness_percentage", 101.0, "above 100.0"),
        ("end_to_end", "latency_p95_seconds", -0.1, "below 0.0"),
    ],
)
def test_invalid_metric_ranges_are_rejected(
    level: str,
    metric: str,
    value: float,
    message: str,
) -> None:
    payload = part_b_summary()
    payload["runs"]["1"][level][metric] = value

    with pytest.raises(gate.ReleaseGateError, match=message):
        gate.extract_metrics(payload, "candidate")


def test_gate_rejects_different_question_counts() -> None:
    baseline = gate.extract_metrics(part_b_summary(), "baseline")
    candidate = gate.extract_metrics(
        part_b_summary(),
        "candidate",
    )
    candidate["successful_requests"] = 29

    with pytest.raises(gate.ReleaseGateError, match="different numbers of questions"):
        gate.evaluate_gate(baseline, candidate, thresholds())


def test_invalid_threshold_configurations(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "thresholds": {
                    "max_recall_at_5_regression_percentage_points": -1,
                    "min_faithfulness_percentage": 70,
                    "max_p95_latency_seconds": 5,
                    "abstention_not_lower_than_baseline": True,
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(gate.ReleaseGateError, match="0 to 100"):
        gate.load_thresholds(invalid)

    missing = tmp_path / "missing.yaml"
    missing.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "thresholds": {
                    "max_recall_at_5_regression_percentage_points": 2,
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(gate.ReleaseGateError, match="missing threshold keys"):
        gate.load_thresholds(missing)

    wrong_type = tmp_path / "wrong-type.yaml"
    wrong_type.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "thresholds": {
                    "max_recall_at_5_regression_percentage_points": 2,
                    "min_faithfulness_percentage": 70,
                    "max_p95_latency_seconds": 5,
                    "abstention_not_lower_than_baseline": "yes",
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(gate.ReleaseGateError, match="true or false"):
        gate.load_thresholds(wrong_type)


def test_write_report_and_cli_exit(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    config_path = tmp_path / "gate.yaml"
    baseline_path.write_text(
        json.dumps(part_b_summary()),
        encoding="utf-8",
    )
    candidate_path.write_text(
        json.dumps(part_b_summary(recall=0.63, faithfulness=75, p95=4.5, abstention=10)),
        encoding="utf-8",
    )
    config_path.write_text(
        yaml.safe_dump({"schema_version": 1, "thresholds": thresholds()}),
        encoding="utf-8",
    )

    exit_code = gate.main([
        "--baseline", str(baseline_path),
        "--candidate", str(candidate_path),
        "--config", str(config_path),
        "--out", str(tmp_path / "reports"),
        "--name", "passing",
    ])

    assert exit_code == 0
    assert (tmp_path / "reports" / "passing.json").is_file()


def test_cli_returns_nonzero_for_a_threshold_failure(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    config_path = tmp_path / "gate.yaml"
    baseline_path.write_text(json.dumps(part_b_summary()), encoding="utf-8")
    candidate_path.write_text(
        json.dumps(
            part_b_summary(
                recall=0.61,
                faithfulness=69,
                p95=5.1,
                abstention=7,
            )
        ),
        encoding="utf-8",
    )
    config_path.write_text(
        yaml.safe_dump({"schema_version": 1, "thresholds": thresholds()}),
        encoding="utf-8",
    )

    exit_code = gate.main([
        "--baseline", str(baseline_path),
        "--candidate", str(candidate_path),
        "--config", str(config_path),
        "--out", str(tmp_path / "reports"),
        "--name", "failing",
    ])

    assert exit_code == 1
