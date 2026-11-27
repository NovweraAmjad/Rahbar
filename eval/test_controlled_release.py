from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest
import yaml

from eval import run_controlled_release as release

API_IMAGE = (
    "docker.io/library/import-2026-11-22@sha256:"
    "105c9f7e8b0fc40ff81aceae95517e42d78fb9ce00afa7dcb8a17b74f63a7737"
)


def _copy_manifests(tmp_path: Path) -> tuple[Path, Path]:
    base = tmp_path / "base"
    controlled = tmp_path / "controlled-release"
    shutil.copytree(release.BASE_DIR, base)
    shutil.copytree(release.RELEASE_DIR, controlled)
    return base, controlled


def test_release_manifests_are_valid_and_isolated() -> None:
    result = release.validate_manifests()

    assert result["valid"] is True
    assert result["live_service"] == "api"
    assert result["candidate_service"] == "api-candidate"
    assert result["candidate_collection"] != "rahbar"
    assert result["live_max_unavailable"] == 0


def test_evaluation_network_policy_allows_both_api_and_chroma_ingress() -> None:
    policies = release.load_yaml_documents(
        release.BASE_DIR / "networkpolicy-allow.yaml"
    )
    by_name = {
        policy["metadata"]["name"]: policy
        for policy in policies
        if policy.get("kind") == "NetworkPolicy"
    }
    api_ingress = by_name["allow-api-ingress"]["spec"]["ingress"][0]["from"]
    chroma_ingress = by_name["allow-chroma-ingress"]["spec"]["ingress"][0]["from"]
    evaluation_egress = by_name["allow-evaluation-egress"]["spec"]["egress"]

    assert {"podSelector": {"matchLabels": {"app": "part-b-evaluation"}}} in api_ingress
    assert {
        "podSelector": {"matchLabels": {"app": "part-b-evaluation"}}
    } in chroma_ingress
    assert {
        "podSelector": {"matchLabels": {"app": "api"}}
    } in evaluation_egress[0]["to"]
    assert any(
        {
            "podSelector": {"matchLabels": {"app": "chroma"}}
        } in rule.get("to", [])
        for rule in evaluation_egress
    )


def test_manifest_validation_rejects_candidate_using_live_collection(
    tmp_path: Path,
) -> None:
    base, controlled = _copy_manifests(tmp_path)
    config_path = controlled / "candidate-configmap.yaml"
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    document["data"]["CHROMA_COLLECTION"] = "rahbar"
    config_path.write_text(yaml.safe_dump(document), encoding="utf-8")

    try:
        release.validate_manifests(base, controlled)
    except release.ControlledReleaseError as exc:
        assert "own Chroma collection" in str(exc)
    else:
        raise AssertionError("candidate reuse of the live collection was accepted")


def test_manifest_validation_rejects_nonzero_live_unavailable(
    tmp_path: Path,
) -> None:
    base, controlled = _copy_manifests(tmp_path)
    api_path = base / "api.yaml"
    documents = list(yaml.safe_load_all(api_path.read_text(encoding="utf-8")))
    deployment = next(item for item in documents if item["kind"] == "Deployment")
    deployment["spec"]["strategy"]["rollingUpdate"]["maxUnavailable"] = 1
    api_path.write_text(yaml.safe_dump_all(documents), encoding="utf-8")

    try:
        release.validate_manifests(base, controlled)
    except release.ControlledReleaseError as exc:
        assert "maxUnavailable: 0" in str(exc)
    else:
        raise AssertionError("unsafe live rolling-update strategy was accepted")


def test_manifest_validation_requires_pinned_candidate_image_and_eval_identity(
    tmp_path: Path,
) -> None:
    base, controlled = _copy_manifests(tmp_path)
    api_path = controlled / "candidate-api.yaml"
    documents = list(yaml.safe_load_all(api_path.read_text(encoding="utf-8")))
    deployment = next(item for item in documents if item["kind"] == "Deployment")
    deployment["spec"]["template"]["spec"]["containers"][0]["image"] = "rahbar-api:dev"
    api_path.write_text(yaml.safe_dump_all(documents), encoding="utf-8")

    with pytest.raises(release.ControlledReleaseError, match="pinned by SHA-256"):
        release.validate_manifests(base, controlled)


def _gate_report(*, passed: bool) -> dict:
    return {
        "decision": "PASS" if passed else "FAIL",
        "candidate_result": str(Path("candidate-summary.json").resolve()),
        "gate": {
            "passed": passed,
            "checks": [
                {"metric": "recall_at_5_regression", "passed": passed},
                {"metric": "faithfulness", "passed": passed},
            ],
        },
    }


def _part_b_result(
    *,
    recall: float,
    faithfulness: float,
    latency: float,
    abstention: float,
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
            },
            "component": {
                "baseline_recall_at_3": 0.7,
                "baseline_recall_at_5": recall,
                "baseline_mrr": 0.6,
                "baseline_ndcg_at_5": 0.7,
                "typo_recall_at_3": 0.6,
                "typo_recall_at_5": 0.7,
                "typo_mrr": 0.5,
                "typo_ndcg_at_5": 0.6,
            },
            "end_to_end": {
                "faithfulness_percentage": faithfulness,
                "abstention_correctness_percentage": abstention,
                "citation_validity_percentage": 100.0,
                "latency_p50_seconds": 1.0,
                "latency_p95_seconds": latency,
                "successful_requests": 30,
                "request_failures": 0,
            },
        }

    return {
        "runs": {"1": run(), "2": run()},
        "metadata": {
            "api_base_url": "http://api-candidate:8000",
            "api_image_reference": API_IMAGE,
            "embedding_model": "nomic-embed-text",
            "embedding_ollama_url": "http://172.23.112.1:11434",
        },
    }


def test_promotion_decision_requires_every_gate_check_to_pass() -> None:
    decision, reasons = release.promotion_decision(_gate_report(passed=True))

    assert decision is True
    assert reasons == []

    failed = _gate_report(passed=True)
    failed["gate"]["checks"][1]["passed"] = False
    decision, reasons = release.promotion_decision(failed)

    assert decision is False
    assert "one or more configured release checks failed" in reasons


def test_promotion_decision_blocks_failed_or_malformed_gate_reports() -> None:
    for report in (_gate_report(passed=False), {}, {"decision": "PASS", "gate": {"passed": True}}):
        decision, reasons = release.promotion_decision(report)
        assert decision is False
        assert reasons


def test_promotion_refuses_failed_gate_before_any_cluster_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        release,
        "run_command",
        lambda *_args, **_kwargs: pytest.fail("failed gate must block kubectl"),
    )
    report_path = tmp_path / "gate-failed.json"
    report_path.write_text(json.dumps(_gate_report(passed=False)), encoding="utf-8")

    with pytest.raises(release.ControlledReleaseError, match="promotion blocked"):
        release.promote(
            gate_report_path=report_path,
            candidate_result_path=candidate_path,
            record_path=tmp_path / "promotion.json",
            context="does-not-matter",
        )


def test_promotion_artifacts_must_recompute_to_a_pass(
    tmp_path: Path,
) -> None:
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    baseline_path.write_text(
        json.dumps(
            _part_b_result(
                recall=0.8,
                faithfulness=80,
                latency=4,
                abstention=90,
            )
        ),
        encoding="utf-8",
    )
    candidate_path.write_text(
        json.dumps(
            _part_b_result(
                recall=0.79,
                faithfulness=75,
                latency=4.5,
                abstention=90,
            )
        ),
        encoding="utf-8",
    )
    thresholds = {
        "max_recall_at_5_regression_percentage_points": 2.0,
        "min_faithfulness_percentage": 70.0,
        "max_p95_latency_seconds": 5.0,
        "abstention_not_lower_than_baseline": True,
    }
    threshold_config = tmp_path / "release-gate.yaml"
    threshold_config.write_text(
        yaml.safe_dump({"schema_version": 1, "thresholds": thresholds}),
        encoding="utf-8",
    )
    from eval import run_release_gate

    baseline = run_release_gate.extract_metrics(
        json.loads(baseline_path.read_text(encoding="utf-8")),
        "baseline",
    )
    candidate = run_release_gate.extract_metrics(
        json.loads(candidate_path.read_text(encoding="utf-8")),
        "candidate",
    )
    recomputed = run_release_gate.evaluate_gate(baseline, candidate, thresholds)
    report = {
        "baseline_result": str(baseline_path.resolve()),
        "candidate_result": str(candidate_path.resolve()),
        "threshold_config": str(threshold_config.resolve()),
        "thresholds": thresholds,
        "baseline_metrics": baseline,
        "candidate_metrics": candidate,
        "gate": recomputed,
        "decision": "PASS",
    }

    config = {
        "EMBED_MODEL": "nomic-embed-text",
        "OLLAMA_URL": "http://172.23.112.1:11434",
    }
    assert release.verify_gate_artifacts(
        report,
        candidate_path,
        config,
        candidate_image=API_IMAGE,
    ) is True
    report["gate"]["passed"] = False
    assert release.verify_gate_artifacts(
        report,
        candidate_path,
        config,
        candidate_image=API_IMAGE,
    ) is False

    report["gate"] = recomputed
    candidate_payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate_payload["metadata"]["api_base_url"] = "http://api:8000"
    candidate_path.write_text(json.dumps(candidate_payload), encoding="utf-8")
    assert release.verify_gate_artifacts(
        report,
        candidate_path,
        config,
        candidate_image=API_IMAGE,
    ) is False

    candidate_path.write_text(
        json.dumps(
            _part_b_result(
                recall=0.79,
                faithfulness=75,
                latency=4.5,
                abstention=90,
            )
        ),
        encoding="utf-8",
    )
    threshold_config.write_text(
        yaml.safe_dump({
            "schema_version": 1,
            "thresholds": {**thresholds, "min_faithfulness_percentage": 100.0},
        }),
        encoding="utf-8",
    )
    assert release.verify_gate_artifacts(
        report,
        candidate_path,
        config,
        candidate_image=API_IMAGE,
    ) is False


def test_promotion_patch_uses_candidate_values_and_zero_unavailable() -> None:
    patch = release.promotion_patch(
        image="rahbar-api@sha256:abc123",
        candidate_config={
            "CHROMA_COLLECTION": "rahbar-candidate-c1",
            "EMBED_MODEL": "nomic-embed-text:latest",
        },
        gate_report_name="gate-pass.json",
        timestamp="2026-11-22T00:00:00+00:00",
    )

    assert patch["spec"]["strategy"]["rollingUpdate"]["maxUnavailable"] == 0
    container = patch["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "rahbar-api@sha256:abc123"
    assert container["env"] == [
        {"name": "CHROMA_COLLECTION", "value": "rahbar-candidate-c1"},
        {"name": "EMBED_MODEL", "value": "nomic-embed-text:latest"},
    ]
