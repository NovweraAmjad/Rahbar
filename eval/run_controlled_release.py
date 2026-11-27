#!/usr/bin/env python3
"""Validate and explicitly control the Assignment 03 C3 Kubernetes release."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Sequence

import httpx
import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import run_release_gate as release_gate  # noqa: E402

BASE_DIR = ROOT / "k8s" / "base"
RELEASE_DIR = ROOT / "k8s" / "controlled-release"
DEFAULT_RELEASE_ROOT = ROOT / "eval" / "results" / "controlled-release"

LIVE_API_MANIFEST = BASE_DIR / "api.yaml"
LIVE_CONFIG_MANIFEST = BASE_DIR / "configmap.yaml"
CANDIDATE_CONFIG_MANIFEST = RELEASE_DIR / "candidate-configmap.yaml"
CANDIDATE_API_MANIFEST = RELEASE_DIR / "candidate-api.yaml"
INDEXER_JOB_MANIFEST = RELEASE_DIR / "candidate-indexer-job.yaml"
EVALUATOR_JOB_MANIFEST = RELEASE_DIR / "part-b-candidate-job.yaml"
EVALUATION_PVC_MANIFEST = RELEASE_DIR / "evaluation-results-pvc.yaml"
NETWORK_POLICY_MANIFEST = RELEASE_DIR / "networkpolicy-candidate.yaml"


class ControlledReleaseError(RuntimeError):
    pass


def load_yaml_documents(path: Path) -> list[dict[str, Any]]:
    try:
        documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError) as exc:
        raise ControlledReleaseError(f"cannot load YAML manifest {path}: {exc}") from exc
    result = [item for item in documents if item is not None]
    if not result or any(not isinstance(item, dict) for item in result):
        raise ControlledReleaseError(f"manifest must contain YAML object(s): {path}")
    return result


def _named_document(
    documents: list[dict[str, Any]],
    kind: str,
    name: str,
    path: Path,
) -> dict[str, Any]:
    matches = [
        item
        for item in documents
        if item.get("kind") == kind
        and item.get("metadata", {}).get("name") == name
    ]
    if len(matches) != 1:
        raise ControlledReleaseError(
            f"{path} must contain exactly one {kind}/{name}; found {len(matches)}"
        )
    return matches[0]


def _config_data(document: dict[str, Any], name: str) -> dict[str, str]:
    data = document.get("data")
    if not isinstance(data, dict) or not data:
        raise ControlledReleaseError(f"{name} ConfigMap must have data")
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in data.items()):
        raise ControlledReleaseError(f"{name} ConfigMap data must be string-valued")
    return data


def _immutable_image_reference(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    image, separator, digest = value.partition("@sha256:")
    return (
        bool(image)
        and separator == "@sha256:"
        and len(digest) == 64
        and all(character in "0123456789abcdefABCDEF" for character in digest)
    )


def validate_manifests(
    base_dir: Path = BASE_DIR,
    release_dir: Path = RELEASE_DIR,
) -> dict[str, Any]:
    live_api_path = base_dir / "api.yaml"
    live_config_path = base_dir / "configmap.yaml"
    candidate_config_path = release_dir / "candidate-configmap.yaml"
    candidate_api_path = release_dir / "candidate-api.yaml"
    indexer_job_path = release_dir / "candidate-indexer-job.yaml"
    evaluator_job_path = release_dir / "part-b-candidate-job.yaml"
    pvc_path = release_dir / "evaluation-results-pvc.yaml"
    network_policy_path = release_dir / "networkpolicy-candidate.yaml"

    live_api_docs = load_yaml_documents(live_api_path)
    live_service = _named_document(live_api_docs, "Service", "api", live_api_path)
    live_deployment = _named_document(live_api_docs, "Deployment", "api", live_api_path)
    live_config = _named_document(
        load_yaml_documents(live_config_path), "ConfigMap", "rahbar-config", live_config_path
    )
    candidate_config = _named_document(
        load_yaml_documents(candidate_config_path),
        "ConfigMap",
        "rahbar-candidate-config",
        candidate_config_path,
    )
    candidate_api_docs = load_yaml_documents(candidate_api_path)
    candidate_service = _named_document(
        candidate_api_docs, "Service", "api-candidate", candidate_api_path
    )
    candidate_deployment = _named_document(
        candidate_api_docs, "Deployment", "api-candidate", candidate_api_path
    )
    candidate_indexer = _named_document(
        load_yaml_documents(indexer_job_path), "Job", "indexer-candidate", indexer_job_path
    )
    candidate_evaluator = _named_document(
        load_yaml_documents(evaluator_job_path), "Job", "part-b-candidate", evaluator_job_path
    )
    _named_document(
        load_yaml_documents(pvc_path), "PersistentVolumeClaim",
        "part-b-candidate-results", pvc_path,
    )
    policies = load_yaml_documents(network_policy_path)

    live_spec = live_deployment.get("spec", {})
    live_strategy = live_spec.get("strategy", {})
    live_update = live_strategy.get("rollingUpdate", {})
    if live_strategy.get("type") != "RollingUpdate" or str(
        live_update.get("maxUnavailable")
    ) != "0":
        raise ControlledReleaseError(
            "live API Deployment must use RollingUpdate with maxUnavailable: 0"
        )
    live_selector = live_spec.get("selector", {}).get("matchLabels")
    if not live_selector or live_service.get("spec", {}).get("selector") != live_selector:
        raise ControlledReleaseError("live API Service must select the live API Deployment")

    candidate_data = _config_data(candidate_config, "candidate")
    live_data = _config_data(live_config, "live")
    required = {
        "RETRIEVER",
        "LLM_PROVIDER",
        "EMBED_MODEL",
        "CHROMA_HOST",
        "CHROMA_PORT",
        "CHROMA_COLLECTION",
        "TOP_K",
        "LLM_MODEL",
        "OLLAMA_URL",
        "CHUNK_SIZE",
        "CHUNK_OVERLAP",
    }
    missing = sorted(required - set(candidate_data))
    if missing:
        raise ControlledReleaseError(
            f"candidate ConfigMap is missing: {', '.join(missing)}"
        )
    if not candidate_data["CHROMA_COLLECTION"] or (
        candidate_data["CHROMA_COLLECTION"] == live_data.get("CHROMA_COLLECTION")
    ):
        raise ControlledReleaseError("candidate must use its own Chroma collection")
    if not candidate_data["EMBED_MODEL"] or not candidate_data["LLM_MODEL"]:
        raise ControlledReleaseError("candidate embedding and generation models must be pinned")
    if candidate_data.get("CACHE_PREFIX") == live_data.get("CACHE_PREFIX"):
        raise ControlledReleaseError("candidate semantic cache prefix must be isolated")
    try:
        size = int(candidate_data["CHUNK_SIZE"])
        overlap = int(candidate_data["CHUNK_OVERLAP"])
    except ValueError as exc:
        raise ControlledReleaseError("candidate chunk settings must be integers") from exc
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ControlledReleaseError("candidate chunk size/overlap are invalid")

    candidate_spec = candidate_deployment.get("spec", {})
    candidate_labels = candidate_spec.get("selector", {}).get("matchLabels")
    service_labels = candidate_service.get("spec", {}).get("selector")
    if (
        not candidate_labels
        or candidate_labels != service_labels
        or candidate_labels == live_selector
    ):
        raise ControlledReleaseError("candidate Service and Deployment selectors must be isolated")
    candidate_pod = candidate_spec.get("template", {}).get("spec", {})
    candidate_container = _container(candidate_pod, "api", "candidate API")
    candidate_image = candidate_container.get("image")
    if not _immutable_image_reference(candidate_image):
        raise ControlledReleaseError("candidate API image must be pinned by SHA-256 digest")
    env_from = candidate_container.get("envFrom", [])
    if not any(
        item.get("configMapRef", {}).get("name") == "rahbar-candidate-config"
        for item in env_from
    ):
        raise ControlledReleaseError("candidate API must use rahbar-candidate-config")

    indexer_spec = candidate_indexer["spec"]["template"]["spec"]
    if not any(
        item.get("configMapRef", {}).get("name") == "rahbar-candidate-config"
        for item in indexer_spec["containers"][0].get("envFrom", [])
    ):
        raise ControlledReleaseError("candidate indexer must use the candidate ConfigMap")
    eval_spec = candidate_evaluator["spec"]["template"]["spec"]
    evaluator = _container(eval_spec, "evaluator", "Part B evaluator")
    command_and_args = " ".join(
        [*evaluator.get("command", []), *evaluator.get("args", [])]
    )
    if (
        not any(
            runner in command_and_args
            for runner in (
                "/opt/rahbar/eval/run_part_b_suite.py",
                "/opt/rahbar/eval/run_k8s_eval.py",
            )
        )
        or "http://api-candidate:8000" not in command_and_args
    ):
        raise ControlledReleaseError(
            "Part B evaluation Job must run the full suite against api-candidate"
        )
    args = evaluator.get("args", [])
    image_argument = args.index("--api-image") + 1 if (
        isinstance(args, list) and "--api-image" in args
    ) else -1
    if image_argument < 0 or image_argument >= len(args) or args[image_argument] != candidate_image:
        raise ControlledReleaseError(
            "Part B evaluation Job must record the immutable candidate API image"
        )
    environment = evaluator.get("env", [])
    if not any(
        item.get("name") == "PYTHONHASHSEED" and item.get("value") == "20261121"
        for item in environment
        if isinstance(item, dict)
    ):
        raise ControlledReleaseError(
            "Part B evaluation Job must set PYTHONHASHSEED before Python starts"
        )
    if not any(
        item.get("configMapRef", {}).get("name") == "rahbar-candidate-config"
        for item in evaluator.get("envFrom", [])
    ):
        raise ControlledReleaseError("Part B evaluation Job must use candidate model settings")
    if "/opt/rahbar/eval/run_k8s_eval.py" in command_and_args:
        for required_argument in ("--baseline", "--thresholds"):
            if required_argument not in args:
                raise ControlledReleaseError(
                    "Kubernetes Part B evaluation Job is missing "
                    f"{required_argument}"
                )

    required_policies = {
        "allow-candidate-api-ingress",
        "allow-candidate-api-egress",
        "allow-candidate-api-chroma-ingress",
        "allow-candidate-indexer-egress",
        "allow-candidate-indexer-chroma-ingress",
        "allow-candidate-redis-ingress",
        "allow-part-b-candidate-egress",
        "allow-part-b-candidate-chroma-ingress",
    }
    policy_names = {
        item.get("metadata", {}).get("name")
        for item in policies
        if item.get("kind") == "NetworkPolicy"
    }
    if required_policies - policy_names:
        raise ControlledReleaseError(
            "candidate network policies missing: "
            + ", ".join(sorted(required_policies - policy_names))
        )

    return {
        "valid": True,
        "live_service": live_service["metadata"]["name"],
        "candidate_service": candidate_service["metadata"]["name"],
        "candidate_collection": candidate_data["CHROMA_COLLECTION"],
        "candidate_embedding_model": candidate_data["EMBED_MODEL"],
        "candidate_generation_model": candidate_data["LLM_MODEL"],
        "candidate_image": candidate_image,
        "live_max_unavailable": 0,
        "evaluation_job": candidate_evaluator["metadata"]["name"],
    }


def _container(pod_spec: dict[str, Any], name: str, description: str) -> dict[str, Any]:
    containers = pod_spec.get("containers")
    if not isinstance(containers, list):
        raise ControlledReleaseError(f"{description} has no containers")
    matches = [item for item in containers if item.get("name") == name]
    if len(matches) != 1:
        raise ControlledReleaseError(
            f"{description} must have exactly one container named {name}"
        )
    return matches[0]


def promotion_decision(gate_report: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    gate = gate_report.get("gate")
    checks = gate.get("checks") if isinstance(gate, dict) else None
    if gate_report.get("decision") != "PASS":
        reasons.append("release gate report decision is not PASS")
    if not isinstance(gate, dict) or gate.get("passed") is not True:
        reasons.append("release gate did not mark the candidate as passed")
    if not isinstance(checks, list) or not checks:
        reasons.append("release gate report has no check results")
    elif any(not isinstance(item, dict) or item.get("passed") is not True for item in checks):
        reasons.append("one or more configured release checks failed")
    if not isinstance(gate_report.get("candidate_result"), str):
        reasons.append("release gate report does not identify a candidate result file")
    return not reasons, reasons


def verify_gate_artifacts(
    gate_report: dict[str, Any],
    candidate_result_path: Path,
    candidate_config: dict[str, str],
    candidate_image: str | None = None,
) -> bool:
    allowed, _reasons = promotion_decision(gate_report)
    if not allowed or not candidate_result_path.is_file():
        return False
    baseline_path = gate_report.get("baseline_result")
    if not isinstance(baseline_path, str) or not Path(baseline_path).is_file():
        return False
    if not resolve_same_path(gate_report["candidate_result"], candidate_result_path):
        return False
    thresholds = gate_report.get("thresholds")
    if not isinstance(thresholds, dict):
        return False
    threshold_config = gate_report.get("threshold_config")
    if not isinstance(threshold_config, str):
        return False
    try:
        configured_thresholds = release_gate.load_thresholds(Path(threshold_config))
        if configured_thresholds != thresholds:
            return False
        baseline_metrics = release_gate.extract_metrics(
            release_gate.read_json(Path(baseline_path)),
            "baseline",
        )
        candidate_payload = release_gate.read_json(candidate_result_path)
        metadata = candidate_payload.get("metadata")
        api_base_url = metadata.get("api_base_url") if isinstance(metadata, dict) else None
        if (
            not isinstance(metadata, dict)
            or not isinstance(api_base_url, str)
            or api_base_url.rstrip("/") != "http://api-candidate:8000"
            or metadata.get("embedding_model") != candidate_config.get("EMBED_MODEL")
            or metadata.get("embedding_ollama_url")
            != candidate_config.get("OLLAMA_URL")
            or metadata.get("api_image_reference") != candidate_image
            or not _immutable_image_reference(metadata.get("api_image_reference"))
        ):
            return False
        candidate_metrics = release_gate.extract_metrics(candidate_payload, "candidate")
        recomputed = release_gate.evaluate_gate(
            baseline_metrics,
            candidate_metrics,
            thresholds,
        )
    except (release_gate.ReleaseGateError, KeyError, TypeError):
        return False
    if (
        gate_report.get("baseline_metrics") != baseline_metrics
        or gate_report.get("candidate_metrics") != candidate_metrics
        or gate_report.get("gate") != recomputed
        or gate_report.get("decision") != ("PASS" if recomputed["passed"] else "FAIL")
    ):
        return False
    return recomputed["passed"]


def promotion_patch(
    *,
    image: str,
    candidate_config: dict[str, str],
    gate_report_name: str,
    timestamp: str,
) -> dict[str, Any]:
    if not image.strip():
        raise ControlledReleaseError("candidate image must not be empty")
    if not candidate_config:
        raise ControlledReleaseError("candidate config must not be empty")
    return {
        "spec": {
            "strategy": {
                "type": "RollingUpdate",
                "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
            },
            "template": {
                "metadata": {
                    "annotations": {
                        "rahbar.release/gate-report": gate_report_name,
                        "rahbar.release/promoted-at": timestamp,
                    }
                },
                "spec": {
                    "containers": [
                        {
                            "name": "api",
                            "image": image,
                            "env": [
                                {"name": key, "value": value}
                                for key, value in sorted(candidate_config.items())
                            ],
                        }
                    ]
                },
            },
        }
    }


def kubectl_args(context: str | None, *args: str) -> list[str]:
    command = ["kubectl"]
    if context:
        command.extend(["--context", context])
    command.extend(args)
    return command


def run_command(
    command: Sequence[str],
    *,
    timeout: int = 180,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(command),
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ControlledReleaseError(f"command failed to execute: {command[0]}: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise ControlledReleaseError(
            f"command failed ({result.returncode}): {' '.join(command)}"
            + (f"\n{detail[-3000:]}" if detail else "")
        )
    return result


def kubectl_json(context: str | None, *args: str) -> dict[str, Any]:
    result = run_command(kubectl_args(context, *args))
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ControlledReleaseError(f"kubectl returned invalid JSON: {' '.join(args)}") from exc
    if not isinstance(payload, dict):
        raise ControlledReleaseError(f"kubectl JSON must be an object: {' '.join(args)}")
    return payload


def resolve_same_path(left: str, right: Path) -> bool:
    return Path(left).resolve().as_posix().casefold() == right.resolve().as_posix().casefold()


def promote(
    *,
    gate_report_path: Path,
    candidate_result_path: Path,
    record_path: Path,
    context: str | None,
    namespace: str = "rahbar",
    rollout_timeout: str = "5m",
) -> Path:
    manifest_validation = validate_manifests()
    try:
        gate_report = json.loads(gate_report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ControlledReleaseError(f"cannot load gate report: {exc}") from exc
    if not isinstance(gate_report, dict):
        raise ControlledReleaseError("gate report must be a JSON object")
    allowed, reasons = promotion_decision(gate_report)
    if not allowed:
        raise ControlledReleaseError("promotion blocked: " + "; ".join(reasons))
    if not candidate_result_path.is_file():
        raise ControlledReleaseError(f"candidate result file does not exist: {candidate_result_path}")
    manifest_config = _config_data(
        _named_document(
            load_yaml_documents(CANDIDATE_CONFIG_MANIFEST),
            "ConfigMap",
            "rahbar-candidate-config",
            CANDIDATE_CONFIG_MANIFEST,
        ),
        "candidate",
    )
    if not verify_gate_artifacts(
        gate_report,
        candidate_result_path,
        manifest_config,
        candidate_image=manifest_validation["candidate_image"],
    ):
        raise ControlledReleaseError(
            "gate inputs or candidate provider/config provenance no longer verify as the passing candidate result"
        )

    live_config = kubectl_json(
        context, "get", "configmap", "rahbar-config", "-n", namespace, "-o", "json"
    ).get("data")
    candidate_config = kubectl_json(
        context,
        "get",
        "configmap",
        "rahbar-candidate-config",
        "-n",
        namespace,
        "-o",
        "json",
    ).get("data")
    live_deployment = kubectl_json(
        context, "get", "deployment", "api", "-n", namespace, "-o", "json"
    )
    candidate_deployment = kubectl_json(
        context, "get", "deployment", "api-candidate", "-n", namespace, "-o", "json"
    )
    evaluator_job = kubectl_json(
        context, "get", "job", "part-b-candidate", "-n", namespace, "-o", "json"
    )
    if not isinstance(live_config, dict) or not isinstance(candidate_config, dict):
        raise ControlledReleaseError("live/candidate ConfigMaps lack data")
    manifest_config = _config_data(
        _named_document(
            load_yaml_documents(CANDIDATE_CONFIG_MANIFEST),
            "ConfigMap",
            "rahbar-candidate-config",
            CANDIDATE_CONFIG_MANIFEST,
        ),
        "candidate",
    )
    if candidate_config != manifest_config:
        raise ControlledReleaseError("live candidate ConfigMap differs from validated manifest")
    strategy = live_deployment.get("spec", {}).get("strategy", {})
    if strategy.get("type") != "RollingUpdate" or str(
        strategy.get("rollingUpdate", {}).get("maxUnavailable")
    ) != "0":
        raise ControlledReleaseError("live Deployment does not have maxUnavailable: 0")
    conditions = candidate_deployment.get("status", {}).get("conditions", [])
    if not any(
        item.get("type") == "Available" and item.get("status") == "True"
        for item in conditions
    ):
        raise ControlledReleaseError("candidate API Deployment is not Available")
    if evaluator_job.get("status", {}).get("succeeded", 0) < 1:
        raise ControlledReleaseError("Part B evaluation Job has not completed successfully")
    if (
        candidate_config.get("CHROMA_COLLECTION") == live_config.get("CHROMA_COLLECTION")
        or candidate_config.get("EMBED_MODEL") == ""
    ):
        raise ControlledReleaseError("candidate collection/model configuration is invalid")

    containers = candidate_deployment.get("spec", {}).get("template", {}).get("spec", {}).get(
        "containers", []
    )
    candidate_container = _container(
        {"containers": containers}, "api", "candidate API Deployment"
    )
    image = candidate_container.get("image")
    if image != manifest_validation["candidate_image"]:
        raise ControlledReleaseError(
            "running candidate Deployment image differs from the validated manifest"
        )
    previous_revision = (
        live_deployment.get("metadata", {}).get("annotations", {}).get(
            "deployment.kubernetes.io/revision"
        )
    )
    previous_image = _container(
        {
            "containers": live_deployment.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        },
        "api",
        "live API Deployment",
    ).get("image")
    previous_config = {
        key: live_config.get(key)
        for key in ("CHROMA_COLLECTION", "EMBED_MODEL", "LLM_MODEL", "LLM_PROVIDER")
    }
    promoted_at = datetime.now(timezone.utc).isoformat()
    patch = promotion_patch(
        image=str(image),
        candidate_config=candidate_config,
        gate_report_name=gate_report_path.name,
        timestamp=promoted_at,
    )
    record = {
        "schema_version": 1,
        "decision": "promotion_in_progress",
        "created_at": promoted_at,
        "namespace": namespace,
        "gate_report": str(gate_report_path.resolve()),
        "candidate_result": str(candidate_result_path.resolve()),
        "previous_revision": previous_revision,
        "previous_image": previous_image,
        "previous_config": previous_config,
        "candidate_image": image,
        "candidate_config": candidate_config,
        "candidate_collection": candidate_config["CHROMA_COLLECTION"],
        "candidate_embedding_model_version": candidate_config["EMBED_MODEL"],
        "live_max_unavailable": 0,
    }
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        run_command(
            kubectl_args(
                context,
                "patch",
                "deployment",
                "api",
                "-n",
                namespace,
                "--type=strategic",
                "--patch",
                json.dumps(patch),
            )
        )
        rollout = run_command(
            kubectl_args(
                context,
                "rollout",
                "status",
                "deployment/api",
                "-n",
                namespace,
                f"--timeout={rollout_timeout}",
            ),
            timeout=360,
        )
        record["decision"] = "promoted"
        record["rollout_status"] = rollout.stdout.strip()
        record["promoted_revision"] = kubectl_json(
            context, "get", "deployment", "api", "-n", namespace, "-o", "json"
        ).get("metadata", {}).get("annotations", {}).get(
            "deployment.kubernetes.io/revision"
        )
    except ControlledReleaseError as exc:
        record["decision"] = "promotion_failed"
        record["error"] = str(exc)
        record["rollout_status"] = "failed or incomplete"
        record_path.write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        raise
    record["completed_at"] = datetime.now(timezone.utc).isoformat()
    record_path.write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return record_path


def rollback(
    *,
    record_path: Path,
    context: str | None,
    rollout_timeout: str = "5m",
) -> Path:
    record = _read_record(record_path)
    revision = record.get("previous_revision")
    command = ["rollout", "undo", "deployment/api", "-n", record["namespace"]]
    if revision is not None:
        command.append(f"--to-revision={revision}")
    undo = run_command(kubectl_args(context, *command))
    status = run_command(
        kubectl_args(
            context,
            "rollout",
            "status",
            "deployment/api",
            "-n",
            record["namespace"],
            f"--timeout={rollout_timeout}",
        ),
        timeout=360,
    )
    record["rollback"] = {
        "status": "completed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "revision": revision,
        "undo_output": undo.stdout.strip(),
        "rollout_status": status.stdout.strip(),
    }
    record_path.write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return record_path


def _read_record(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ControlledReleaseError(f"cannot read release record {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ControlledReleaseError("release record must be a JSON object")
    return payload


def verify_rollback_trace(
    *,
    record_path: Path,
    api: str,
    query: str,
    context: str | None,
    timeout: int = 60,
) -> Path:
    record = _read_record(record_path)
    previous = record.get("previous_config")
    if not isinstance(previous, dict):
        raise ControlledReleaseError("release record has no previous deployment configuration")
    expected_collection = previous.get("CHROMA_COLLECTION")
    expected_embedding = previous.get("EMBED_MODEL")
    if not isinstance(expected_collection, str) or not isinstance(expected_embedding, str):
        raise ControlledReleaseError("previous collection or embedding version is unavailable")
    try:
        response = httpx.post(
            f"{api.rstrip('/')}/api/ask",
            headers={"Host": "rahbar.local"},
            json={"question": query},
            timeout=timeout,
        )
        response.raise_for_status()
        answer = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ControlledReleaseError(f"fresh rollback verification query failed: {exc}") from exc
    trace_id = answer.get("request_id") if isinstance(answer, dict) else None
    if not isinstance(trace_id, str) or not trace_id:
        raise ControlledReleaseError("fresh query response has no request_id")

    deadline = time.monotonic() + 30
    trace: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        logs = run_command(
            kubectl_args(
                context,
                "logs",
                "-l",
                "app=api",
                "-n",
                record["namespace"],
                "--all-containers=true",
                "--max-log-requests=10",
                "--since=2m",
            )
        ).stdout
        for line in logs.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(event, dict)
                and event.get("msg") == "query_trace"
                and event.get("trace_id") == trace_id
            ):
                trace = event
                break
        if trace is not None:
            break
        time.sleep(1)
    if trace is None:
        raise ControlledReleaseError(f"query trace {trace_id} was not found in API pod logs")
    if trace.get("collection_name") != expected_collection:
        raise ControlledReleaseError(
            "rollback trace collection does not match previous configuration: "
            f"{trace.get('collection_name')!r} != {expected_collection!r}"
        )
    if trace.get("embedding_model_version") != expected_embedding:
        raise ControlledReleaseError(
            "rollback trace embedding model does not match previous configuration: "
            f"{trace.get('embedding_model_version')!r} != {expected_embedding!r}"
        )
    verification = {
        "schema_version": 1,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "trace_id": trace_id,
        "expected_previous_collection": expected_collection,
        "observed_collection": trace["collection_name"],
        "expected_previous_embedding_model_version": expected_embedding,
        "observed_embedding_model_version": trace["embedding_model_version"],
        "trace": trace,
        "verified": True,
    }
    output = record_path.with_name("rollback-trace-verification.json")
    output.write_text(
        json.dumps(verification, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Controlled C3 release workflow")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate release manifests without contacting Kubernetes")

    promote_parser = subparsers.add_parser("promote", help="promote only a gate-passing candidate")
    promote_parser.add_argument("--gate-report", required=True, type=Path)
    promote_parser.add_argument("--candidate-result", required=True, type=Path)
    promote_parser.add_argument("--record", type=Path)
    promote_parser.add_argument("--context")
    promote_parser.add_argument("--namespace", default="rahbar")
    promote_parser.add_argument("--rollout-timeout", default="5m")

    rollback_parser = subparsers.add_parser("rollback", help="undo and record a promotion")
    rollback_parser.add_argument("--record", required=True, type=Path)
    rollback_parser.add_argument("--context")
    rollback_parser.add_argument("--rollout-timeout", default="5m")

    verify_parser = subparsers.add_parser(
        "verify-rollback-trace",
        help="send a fresh query and verify its trace uses the prior collection/model",
    )
    verify_parser.add_argument("--record", required=True, type=Path)
    verify_parser.add_argument("--api", default="http://127.0.0.1:18080")
    verify_parser.add_argument("--query", required=True)
    verify_parser.add_argument("--context")
    verify_parser.add_argument("--timeout", type=int, default=60)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            result = validate_manifests()
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "promote":
            record = args.record or (
                DEFAULT_RELEASE_ROOT
                / f"promotion-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
            )
            result_path = promote(
                gate_report_path=args.gate_report,
                candidate_result_path=args.candidate_result,
                record_path=record,
                context=args.context,
                namespace=args.namespace,
                rollout_timeout=args.rollout_timeout,
            )
            print(f"Promotion complete; rollout status recorded in {result_path}")
            return 0
        if args.command == "rollback":
            result_path = rollback(
                record_path=args.record,
                context=args.context,
                rollout_timeout=args.rollout_timeout,
            )
            print(f"Rollback complete; rollout status recorded in {result_path}")
            return 0
        if args.command == "verify-rollback-trace":
            output = verify_rollback_trace(
                record_path=args.record,
                api=args.api,
                query=args.query,
                context=args.context,
                timeout=args.timeout,
            )
            print(f"Rollback trace verified: {output}")
            return 0
    except ControlledReleaseError as exc:
        print(f"controlled release failed: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
