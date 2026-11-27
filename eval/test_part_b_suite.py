from __future__ import annotations

import random
from pathlib import Path

import pytest

from eval import run_part_b_suite as suite


def test_fixed_seeds_are_configured(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONHASHSEED", "20261121")
    seeds = suite.configure_seeds()
    assert seeds["python_random"] == 20261121
    assert seeds["python_hash_seed"] == 20261121
    first_random = random.random()
    suite.configure_seeds()
    assert random.random() == first_random
    assert suite.FIXED_SEED == 20261121


def test_api_image_reference_requires_sha256_digest() -> None:
    digest = "a" * 64
    reference = f"registry.example/rahbar-api@sha256:{digest}"

    assert suite.validate_api_image_reference(reference) == reference
    with pytest.raises(ValueError, match="immutable image reference"):
        suite.validate_api_image_reference("rahbar-api:dev")
    with pytest.raises(ValueError, match="64 hexadecimal characters"):
        suite.validate_api_image_reference("rahbar-api@sha256:xyz")


def test_fixed_hash_seed_must_be_set_before_interpreter_startup(monkeypatch) -> None:
    monkeypatch.delenv("PYTHONHASHSEED", raising=False)

    with pytest.raises(RuntimeError, match="does not fix Python hashing"):
        suite.configure_seeds()


def test_absolute_difference() -> None:
    assert suite.absolute_difference(0.8, 0.75) == pytest.approx(0.05)
    assert suite.absolute_difference(None, 0.75) is None


def test_compare_metrics_reports_absolute_difference() -> None:
    first = {
        "module": {"mean": 0.80},
        "component": {"recall": 0.75},
    }
    second = {
        "module": {"mean": 0.78},
        "component": {"recall": None},
    }
    compared = suite.compare_metrics(first, second)

    assert compared["module"]["mean"]["run_1"] == 0.80
    assert compared["module"]["mean"]["run_2"] == 0.78
    assert compared["module"]["mean"]["absolute_difference"] == pytest.approx(0.02)
    assert compared["component"]["recall"]["absolute_difference"] is None


def test_markdown_table_has_all_levels() -> None:
    comparison = {
        "module": {
            "mean": {
                "run_1": 0.8,
                "run_2": 0.7,
                "absolute_difference": 0.1,
            }
        },
        "component": {
            "recall": {
                "run_1": 0.6,
                "run_2": 0.5,
                "absolute_difference": 0.1,
            }
        },
        "end_to_end": {
            "faithfulness": {
                "run_1": None,
                "run_2": None,
                "absolute_difference": None,
            }
        },
    }
    table = suite.markdown_table(comparison)
    assert "| module | mean |" in table
    assert "| component | recall |" in table
    assert "| end_to_end | faithfulness |" in table


def test_non_overwrite_directory(tmp_path: Path) -> None:
    existing = tmp_path / "part-b-stochasticity-20261121T000000Z"
    existing.mkdir()
    assert existing.exists()


def _complete_run_metrics() -> dict:
    return {
        "module": {
            "mean_paraphrase_cosine": 0.8,
            "mean_unrelated_cosine": 0.3,
            "paraphrase_minus_unrelated": 0.5,
            "paraphrase_pair_count": 15,
            "unrelated_pair_count": 15,
            "embedding_time_mean_seconds": 0.2,
            "embedding_time_p95_seconds": 0.4,
            "largest_determinism_difference": 0.0,
        },
        "component": {
            "baseline_recall_at_3": 0.8,
            "baseline_recall_at_5": 0.9,
            "baseline_mrr": 0.7,
            "baseline_ndcg_at_5": 0.8,
            "typo_recall_at_3": 0.7,
            "typo_recall_at_5": 0.8,
            "typo_mrr": 0.6,
            "typo_ndcg_at_5": 0.7,
            "baseline_request_failures": 0,
            "typo_request_failures": 0,
        },
        "end_to_end": {
            "faithfulness_percentage": 80.0,
            "abstention_correctness_percentage": 100.0,
            "citation_validity_percentage": 100.0,
            "latency_p50_seconds": 1.0,
            "latency_p95_seconds": 2.0,
            "successful_requests": 30,
            "request_failures": 0,
        },
    }


def test_validate_run_metrics_accepts_a_complete_suite() -> None:
    suite.validate_run_metrics(_complete_run_metrics(), 30, 1)


def test_validate_run_metrics_rejects_missing_metric() -> None:
    metrics = _complete_run_metrics()
    metrics["component"]["baseline_recall_at_5"] = None

    with pytest.raises(RuntimeError, match="unavailable or invalid component.baseline_recall_at_5"):
        suite.validate_run_metrics(metrics, 30, 1)


def test_validate_run_metrics_rejects_component_request_failures() -> None:
    metrics = _complete_run_metrics()
    metrics["component"]["typo_request_failures"] = 1

    with pytest.raises(RuntimeError, match="failed component requests"):
        suite.validate_run_metrics(metrics, 30, 1)


def test_validate_run_metrics_rejects_too_few_embedding_pairs() -> None:
    metrics = _complete_run_metrics()
    metrics["module"]["paraphrase_pair_count"] = 14

    with pytest.raises(RuntimeError, match="fewer than 15 pairs"):
        suite.validate_run_metrics(metrics, 30, 1)


def test_validate_run_metrics_rejects_incomplete_end_to_end_run() -> None:
    metrics = _complete_run_metrics()
    metrics["end_to_end"]["request_failures"] = 1
    metrics["end_to_end"]["successful_requests"] = 29

    with pytest.raises(RuntimeError, match="did not complete all end-to-end requests"):
        suite.validate_run_metrics(metrics, 30, 2)
