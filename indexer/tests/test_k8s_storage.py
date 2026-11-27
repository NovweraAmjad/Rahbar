from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "k8s" / "base"
CONTROLLED = ROOT / "k8s" / "controlled-release"
PERSISTENT_PATHS = (
    "INDEXER_STATE_DIR",
    "QUARANTINE_DIR",
    "VERIFICATION_REPORT_DIR",
    "ARCHIVE_DIR",
)


def _read_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _assert_pvc_mount(
    pod_spec: dict[str, Any],
    config: dict[str, Any],
    expected_root: str,
) -> None:
    volumes = {volume["name"]: volume for volume in pod_spec["volumes"]}
    mounts = {
        mount["name"]: mount
        for mount in pod_spec["containers"][0]["volumeMounts"]
    }

    volume = volumes["indexer-data"]
    assert volume["persistentVolumeClaim"]["claimName"] == "rahbar-indexer-data"
    assert mounts["indexer-data"]["mountPath"] == "/var/lib/rahbar-data"
    assert mounts["indexer-data"].get("readOnly", False) is False
    for key in PERSISTENT_PATHS:
        assert config[key].startswith(expected_root)


def test_base_indexer_job_and_cronjob_use_persistent_writable_data() -> None:
    job = _read_yaml(BASE / "indexer-job.yaml")
    cronjob = _read_yaml(BASE / "indexer-cronjob.yaml")
    config = _read_yaml(BASE / "configmap.yaml")["data"]
    cron_pod = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]

    _assert_pvc_mount(
        job["spec"]["template"]["spec"],
        config,
        "/var/lib/rahbar-data/",
    )
    _assert_pvc_mount(cron_pod, config, "/var/lib/rahbar-data/")
    for pod_spec in (job["spec"]["template"]["spec"], cron_pod):
        mounts = {
            mount["name"]: mount
            for mount in pod_spec["containers"][0]["volumeMounts"]
        }
        assert mounts["corpus"]["mountPath"] == "/corpus"
        assert mounts["corpus"]["readOnly"] is True


def test_indexer_pvc_is_in_base_kustomization() -> None:
    pvc = _read_yaml(BASE / "indexer-data-pvc.yaml")
    kustomization = _read_yaml(BASE / "kustomization.yaml")

    assert pvc["kind"] == "PersistentVolumeClaim"
    assert pvc["metadata"]["name"] == "rahbar-indexer-data"
    assert "indexer-data-pvc.yaml" in kustomization["resources"]


def test_candidate_indexer_uses_separate_persistent_state_paths() -> None:
    job = _read_yaml(CONTROLLED / "candidate-indexer-job.yaml")
    config = _read_yaml(CONTROLLED / "candidate-configmap.yaml")["data"]
    pod_spec = job["spec"]["template"]["spec"]

    _assert_pvc_mount(
        pod_spec,
        config,
        "/var/lib/rahbar-data/candidate/",
    )
