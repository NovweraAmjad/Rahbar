from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from eval import run_module_embedding_eval as embedding_eval


HERE = Path(__file__).resolve().parent


def test_pair_dataset_has_at_least_15_pairs_in_each_category() -> None:
    seed, pairs = embedding_eval.load_pairs(
        HERE / "module-embedding-pairs.yaml"
    )
    assert seed == 20261121
    assert sum(pair["category"] == "paraphrase" for pair in pairs) >= 15
    assert sum(pair["category"] == "unrelated" for pair in pairs) >= 15


def test_cosine_similarity_calculation() -> None:
    assert embedding_eval.cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert embedding_eval.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert embedding_eval.cosine_similarity([1.0, 1.0], [-1.0, -1.0]) == pytest.approx(-1.0)


def test_mean_and_percentile_calculation() -> None:
    assert embedding_eval.mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)
    assert embedding_eval.percentile([1.0, 2.0, 3.0], 50.0) == pytest.approx(2.0)
    assert embedding_eval.percentile([1.0, 2.0, 3.0], 95.0) == pytest.approx(2.9)


def test_calculate_metrics_and_gap() -> None:
    pairs = [
        {"id": "p1", "category": "paraphrase", "left": "a", "right": "b"},
        {"id": "p2", "category": "paraphrase", "left": "c", "right": "d"},
        {"id": "u1", "category": "unrelated", "left": "e", "right": "f"},
        {"id": "u2", "category": "unrelated", "left": "g", "right": "h"},
    ]
    vectors = {
        "a": [1.0, 0.0],
        "b": [0.9, 0.1],
        "c": [1.0, 0.0],
        "d": [0.8, 0.2],
        "e": [1.0, 0.0],
        "f": [0.0, 1.0],
        "g": [1.0, 0.0],
        "h": [0.0, -1.0],
    }
    metrics, rows = embedding_eval.calculate_metrics(pairs, vectors)

    assert len(rows) == 4
    assert metrics["paraphrase_pair_count"] == 2
    assert metrics["unrelated_pair_count"] == 2
    assert metrics["embedding_dimension"] == 2
    assert metrics["mean_paraphrase_cosine"] > metrics["mean_unrelated_cosine"]
    assert metrics["paraphrase_minus_unrelated"] == pytest.approx(
        float(metrics["mean_paraphrase_cosine"])
        - float(metrics["mean_unrelated_cosine"])
    )


def test_largest_determinism_difference() -> None:
    first = {"a": [1.0, 2.0, 3.0], "b": [0.1, 0.2]}
    second = {"a": [1.0, 2.01, 3.0], "b": [0.11, 0.2]}
    assert embedding_eval.largest_determinism_difference(first, second) == pytest.approx(0.01)


def test_result_schema_for_saved_summary(tmp_path: Path) -> None:
    summary = {
        "metadata": {
            "generated_at": "2026-11-21T00:00:00+00:00",
            "fixed_seed": 20261121,
            "provider": "Ollama",
            "ollama_url": "http://localhost:11434",
            "embedding_model": "nomic-embed-text",
            "embedding_model_identity": {
                "configured": "nomic-embed-text",
                "name": "nomic-embed-text:latest",
                "digest": "sha256:test",
            },
            "embedding_dimensions": 768,
            "unique_chunk_count": 60,
            "timing_method": "time.perf_counter",
            "metric_definitions": {},
        },
        "metrics": {
            "embedding_dimension": 768,
            "mean_paraphrase_cosine": 0.85,
            "mean_unrelated_cosine": 0.20,
            "paraphrase_minus_unrelated": 0.65,
            "paraphrase_pair_count": 15,
            "unrelated_pair_count": 15,
            "embedding_time_per_chunk_mean_seconds": 0.01,
            "embedding_time_per_chunk_median_seconds": 0.009,
            "embedding_time_per_chunk_p95_seconds": 0.02,
            "largest_determinism_difference": 0.0001,
        },
    }
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(summary), encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))

    assert loaded["metadata"]["provider"] == "Ollama"
    assert loaded["metadata"]["embedding_dimensions"] == 768
    assert loaded["metrics"]["paraphrase_pair_count"] >= 15
    assert loaded["metrics"]["unrelated_pair_count"] >= 15
    assert math.isfinite(loaded["metrics"]["paraphrase_minus_unrelated"])


def test_saved_live_module_embedding_summary() -> None:
    directories = sorted(
        (HERE / "results").glob("module-embedding-*"),
        key=lambda path: path.name,
    )
    if not directories:
        pytest.skip("no live module-embedding result has been recorded")
    summary = json.loads(
        (directories[-1] / "summary.json").read_text(encoding="utf-8")
    )
    metadata = summary["metadata"]
    metrics = summary["metrics"]

    assert metadata["provider"] == "Ollama"
    assert metadata["embedding_model"] == "nomic-embed-text"
    assert metadata["embedding_model_identity"]["digest"]
    assert metadata["embedding_dimensions"] == 768
    assert metrics["embedding_dimension"] == 768
    assert metrics["paraphrase_pair_count"] >= 15
    assert metrics["unrelated_pair_count"] >= 15
    assert math.isfinite(metrics["mean_paraphrase_cosine"])
    assert math.isfinite(metrics["mean_unrelated_cosine"])
    assert math.isfinite(metrics["paraphrase_minus_unrelated"])
    assert metrics["embedding_time_per_chunk_mean_seconds"] >= 0.0
    assert metrics["largest_determinism_difference"] >= 0.0
