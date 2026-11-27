from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from eval import run_part_c1 as c1


def _write_config(path: Path, *, candidate_overlap: int = 50) -> Path:
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "experiment": "chunk-size",
                "change": {
                    "variable": "chunk_size",
                    "unit": "whitespace_tokens",
                    "baseline": {
                        "name": "baseline",
                        "chunk_size": 400,
                        "chunk_overlap": 50,
                    },
                    "candidate": {
                        "name": "candidate",
                        "chunk_size": 800,
                        "chunk_overlap": candidate_overlap,
                    },
                },
                "held_constant": {"TOP_K": "5"},
            }
        ),
        encoding="utf-8",
    )
    return path


def _summary(multiplier: int = 1) -> dict:
    module = {
        "mean_paraphrase_cosine": 0.8,
        "embedding_dimension": 768,
    }
    component = {
        "baseline_recall_at_3": 0.5 * multiplier,
        "baseline_recall_at_5": 0.75 * multiplier,
        "baseline_mrr": 0.4,
        "baseline_ndcg_at_5": 0.6,
        "typo_recall_at_3": 0.4,
        "typo_recall_at_5": 0.6,
        "typo_mrr": 0.3,
        "typo_ndcg_at_5": 0.5,
    }
    end_to_end = {
        "faithfulness_percentage": 50.0 * multiplier,
        "abstention_correctness_percentage": 100.0,
        "citation_validity_percentage": 95.0,
        "latency_p50_seconds": 2.0,
        "latency_p95_seconds": 4.0,
        "successful_requests": 30,
        "request_failures": 0,
    }
    return {
        "metadata": {
            "api_image_reference": (
                "registry.example/rahbar-api@sha256:" + "a" * 64
            )
        },
        "runs": {
            "1": {
                "module": module,
                "component": component,
                "end_to_end": end_to_end,
            },
            "2": {
                "module": module,
                "component": component,
                "end_to_end": end_to_end,
            },
        }
    }


def test_load_config_enforces_one_changed_variable(tmp_path: Path) -> None:
    _, constants, profiles = c1.load_config(_write_config(tmp_path / "valid.yaml"))

    assert constants == {"TOP_K": "5"}
    assert profiles[0].chunk_size == 400
    assert profiles[1].chunk_size == 800
    assert profiles[0].chunk_overlap == profiles[1].chunk_overlap == 50

    with pytest.raises(c1.PartC1Error, match="chunk_overlap must remain constant"):
        c1.load_config(
            _write_config(tmp_path / "invalid.yaml", candidate_overlap=100)
        )


def test_corpus_snapshot_hashes_every_indexed_document(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    (corpus / "raw").mkdir(parents=True)
    (corpus / "batch-b").mkdir()
    (corpus / "raw" / "source.txt").write_text("source", encoding="utf-8")
    (corpus / "batch-b" / "batch.txt").write_text("batch", encoding="utf-8")
    for directory, filename in (("raw", "source.txt"), ("batch-b", "batch.txt")):
        path = corpus / directory / filename
        (path.parent / "_index.json").write_text(
            json.dumps([{"file": filename, "sha256": c1.file_sha256(path)}]),
            encoding="utf-8",
        )

    snapshot = c1.corpus_snapshot(corpus)

    assert snapshot["document_count"] == 2
    assert set(snapshot["files"]) == {
        "raw/_index.json",
        "raw/source.txt",
        "batch-b/_index.json",
        "batch-b/batch.txt",
    }
    assert len(snapshot["sha256"]) == 64


def test_compare_summaries_reports_all_levels_and_delta_direction() -> None:
    comparison = c1.compare_summaries(_summary(1), _summary(2))

    assert set(comparison) == {"module", "component", "end_to_end"}
    recall = comparison["component"]["baseline_recall_at_5"]
    assert recall["baseline_mean"] == 0.75
    assert recall["candidate_mean"] == 1.5
    assert recall["absolute_change_candidate_minus_baseline"] == 0.75
    assert recall["relative_change_percentage"] == 100.0
    assert comparison["module"]["mean_paraphrase_cosine"][
        "absolute_change_candidate_minus_baseline"
    ] == 0


def test_validate_suite_rejects_partial_requests(tmp_path: Path) -> None:
    summary = _summary()
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    assert c1.validate_suite(summary_path, 30) is not None

    summary["runs"]["2"]["end_to_end"]["request_failures"] = 1
    summary["runs"]["2"]["end_to_end"]["successful_requests"] = 29
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(c1.PartC1Error, match="incomplete end-to-end requests"):
        c1.validate_suite(summary_path, 30)


def test_api_image_reference_requires_one_ready_immutable_api_pod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = "a" * 64
    pod_list = {
        "items": [
            {
                "status": {
                    "phase": "Running",
                    "containerStatuses": [
                        {
                            "name": "api",
                            "ready": True,
                            "imageID": f"registry.example/rahbar-api@sha256:{digest}",
                        }
                    ],
                }
            }
        ]
    }
    monkeypatch.setattr(
        c1.tuning,
        "_checked",
        lambda *_args, **_kwargs: type("Result", (), {"stdout": json.dumps(pod_list)})(),
    )

    assert c1.api_image_reference("staging") == (
        f"registry.example/rahbar-api@sha256:{digest}"
    )

    pod_list["items"][0]["status"]["containerStatuses"][0]["imageID"] = "rahbar-api:dev"
    with pytest.raises(c1.PartC1Error, match="not immutable"):
        c1.api_image_reference("staging")
