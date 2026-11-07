#!/usr/bin/env python3
"""Run the isolated, sequential live chunk-size experiment on Minikube."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Sequence
from urllib.parse import quote

import httpx
import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
EVAL_DIR = ROOT / "eval"
EXPERIMENT_DIR = ROOT / "k8s" / "experiments" / "chunk-tuning"
NAMESPACE_MANIFEST = EXPERIMENT_DIR / "namespace.yaml"
DEFAULT_PROFILES = EVAL_DIR / "chunk-tuning.yaml"
DEFAULT_GOLDEN = EVAL_DIR / "golden.yaml"
DEFAULT_BASELINE = EVAL_DIR / "baseline.json"
DEFAULT_OUTPUT_ROOT = EVAL_DIR / "chunk-tuning-runs"
CORPUS_DIR = ROOT / "corpus" / "raw"
CORPUS_VERIFY = ROOT / "corpus" / "fetch.py"
EVALUATOR = EVAL_DIR / "run_eval.py"
NAMESPACE = "rahbar-chunk-tuning"
API_SERVICE = "chunk-api"
CHROMA_SERVICE = "chunk-chroma"
CHROMA_LOCAL_PORT = 18081
CHROMA_QUERY_TIMEOUT = 120
JOB_TIMEOUT_SECONDS = 2 * 60 * 60
API_ROLLOUT_TIMEOUT = 240
PORT_FORWARD_TIMEOUT = 90
EVALUATOR_REQUEST_TIMEOUT_SECONDS = 150
EVALUATION_TIMEOUT = 45 * 60

EXPECTED_PROFILES = (
    ("small", 200, 25),
    ("current", 400, 50),
    ("large", 800, 100),
)
LOCKED_SETTINGS = {
    "RETRIEVER": "chroma",
    "LLM_PROVIDER": "ollama-host",
    "EMBED_MODEL": "nomic-embed-text",
    "CHROMA_HOST": "chunk-chroma",
    "CHROMA_PORT": "8000",
    "CORPUS_DIR": "/corpus",
    "TOP_K": "5",
    "RELEVANCE_FLOOR": "0.5",
    "REDIS_URL": "redis://chunk-redis:6379/0",
    "CACHE_PREFIX": "rahbar:semantic-cache",
    "CACHE_SIMILARITY_THRESHOLD": "0.95",
    "TEMPERATURE": "0.2",
    "LLM_MODEL": "llama3.2:3b",
    "OLLAMA_URL": "http://172.23.112.1:11434",
    "LOG_LEVEL": "INFO",
}
_RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,20}$")
_SUPPORTED_CORPUS_SUFFIXES = {".pdf", ".md", ".html", ".txt"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ExperimentError(RuntimeError):
    """Raised when an experiment cannot be safely or completely run."""


@dataclass(frozen=True)
class ChunkProfile:
    name: str
    chunk_size: int
    chunk_overlap: int


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ExperimentError(f"cannot read YAML file {path}: {exc}") from exc


def load_profiles(path: Path) -> list[ChunkProfile]:
    document = _read_yaml(path)
    if not isinstance(document, dict) or not isinstance(document.get("profiles"), list):
        raise ExperimentError("chunk tuning YAML must contain a profiles list")

    profiles: list[ChunkProfile] = []
    for index, raw in enumerate(document["profiles"], start=1):
        if not isinstance(raw, dict):
            raise ExperimentError(f"profile {index} must be a mapping")
        name = raw.get("name")
        size = raw.get("chunk_size")
        overlap = raw.get("chunk_overlap")
        if not isinstance(name, str) or not name:
            raise ExperimentError(f"profile {index} must have a non-empty name")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise ExperimentError(f"{name}: chunk_size must be a positive integer")
        if isinstance(overlap, bool) or not isinstance(overlap, int) or overlap < 0:
            raise ExperimentError(f"{name}: chunk_overlap must be a non-negative integer")
        if overlap >= size:
            raise ExperimentError(f"{name}: chunk_overlap must be smaller than chunk_size")
        profiles.append(ChunkProfile(name, size, overlap))

    actual = tuple((item.name, item.chunk_size, item.chunk_overlap) for item in profiles)
    if actual != EXPECTED_PROFILES:
        raise ExperimentError(
            "profiles must be exactly small=(200,25), current=(400,50), "
            "large=(800,100), in that order"
        )
    return profiles


def load_common_settings(path: Path) -> dict[str, str]:
    document = _read_yaml(path)
    data = document.get("data") if isinstance(document, dict) else None
    if not isinstance(data, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in data.items()
    ):
        raise ExperimentError("experiment ConfigMap must have string-valued data")
    if data != LOCKED_SETTINGS:
        changed = sorted(
            key
            for key in LOCKED_SETTINGS
            if data.get(key) != LOCKED_SETTINGS[key]
        )
        extra = sorted(set(data) - set(LOCKED_SETTINGS))
        missing = sorted(set(LOCKED_SETTINGS) - set(data))
        raise ExperimentError(
            "experiment settings differ from the locked retrieval configuration "
            f"(changed={changed}, extra={extra}, missing={missing})"
        )
    return dict(data)


def cache_scope(cache_prefix: str, collection: str) -> str:
    """Mirror the API/indexer cache namespace before the :entry: or :generation suffix."""
    return f"{cache_prefix}:{collection}"


def collection_name(run_id: str, profile: ChunkProfile) -> str:
    if not _RUN_ID.fullmatch(run_id):
        raise ExperimentError("run ID must contain 1-21 lowercase letters, digits, or hyphens")
    name = f"rahbar-ct-{run_id}-{profile.name}"
    if len(name) > 63:
        raise ExperimentError("run ID produces a Chroma collection name longer than 63 characters")
    return name


def validate_output_path(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise ExperimentError(f"output path already exists; refusing to overwrite: {path}")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _manifest_corpus_entries(path: Path) -> dict[str, dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ExperimentError(f"cannot read corpus manifest {path}: {exc}") from exc
    expected: dict[str, dict[str, Any]] = {}
    for line in lines:
        if not line.startswith("|"):
            continue
        columns = [column.strip() for column in line.strip("|").split("|")]
        if len(columns) < 7 or columns[2] not in {"yes", "no"}:
            continue
        filename = columns[3].strip("`")
        expected_hash = columns[5].strip("`")
        if not _SHA256.fullmatch(expected_hash):
            continue
        if filename in expected:
            raise ExperimentError(f"corpus manifest {path} repeats {filename}")
        expected[filename] = {
            "doc_version": columns[1],
            "superseded": columns[2] == "yes",
            "sha256": expected_hash,
        }
    if not expected:
        raise ExperimentError(f"corpus manifest {path} has no document checksum rows")
    return expected


def corpus_snapshot(corpus_dir: Path) -> dict[str, Any]:
    """Hash exactly the indexer's manifest-listed inputs and its discovery manifest."""
    root = corpus_dir.resolve()
    index_path = root / "_index.json"
    if not root.is_dir() or not index_path.is_file():
        raise ExperimentError(f"expected fetched corpus and _index.json under {root}")
    try:
        entries = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read corpus _index.json: {exc}") from exc
    if not isinstance(entries, list) or not entries:
        raise ExperimentError("corpus _index.json must contain a non-empty list")
    manifest_entries = _manifest_corpus_entries(root.parent / "MANIFEST.md")

    files: dict[str, str] = {}
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            raise ExperimentError(f"corpus index entry {index} has no file")
        relative = Path(entry["file"])
        if relative.is_absolute() or len(relative.parts) != 1 or relative.name in {"", ".", ".."}:
            raise ExperimentError(f"corpus index entry {index} has an unsafe file path")
        if relative.suffix.lower() not in _SUPPORTED_CORPUS_SUFFIXES:
            raise ExperimentError(f"corpus index entry {index} has unsupported file type")
        if relative.name in files:
            raise ExperimentError(f"corpus _index.json repeats {relative.name}")
        path = root / relative
        if not path.is_file():
            raise ExperimentError(f"corpus file listed in _index.json is missing: {relative.name}")
        actual_hash = _file_sha256(path)
        expected_hash = entry.get("sha256")
        if not isinstance(expected_hash, str) or actual_hash != expected_hash:
            raise ExperimentError(f"corpus SHA-256 mismatch for {relative.name}")
        expected_metadata = manifest_entries.get(relative.name)
        if expected_metadata is None:
            raise ExperimentError(f"corpus file is not listed in MANIFEST.md: {relative.name}")
        if (
            expected_metadata["sha256"] != actual_hash
            or entry.get("doc_version") != expected_metadata["doc_version"]
            or entry.get("superseded") != expected_metadata["superseded"]
        ):
            raise ExperimentError(
                f"corpus index metadata does not match MANIFEST.md for {relative.name}"
            )
        files[relative.name] = actual_hash

    if set(files) != set(manifest_entries):
        raise ExperimentError("corpus _index.json and MANIFEST.md list different document files")
    files["_index.json"] = _file_sha256(index_path)
    digest = hashlib.sha256()
    for name, file_hash in sorted(files.items()):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
    return {"sha256": digest.hexdigest(), "files": dict(sorted(files.items()))}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _run(
    command: Sequence[str],
    *,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ExperimentError(f"required command not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ExperimentError(
            f"command timed out after {timeout} seconds: {' '.join(command)}"
        ) from exc
    except OSError as exc:
        raise ExperimentError(f"cannot execute {command[0]}: {exc}") from exc


def _checked(command: Sequence[str], *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    completed = _run(command, timeout=timeout)
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise ExperimentError(
            f"command failed ({completed.returncode}): {' '.join(command)}"
            + (f"\n{detail[-3000:]}" if detail else "")
        )
    return completed


def _git_commit() -> str:
    result = _checked(["git", "rev-parse", "HEAD"])
    commit = result.stdout.strip()
    if not commit:
        raise ExperimentError("git rev-parse returned an empty commit identifier")
    return commit


def _verify_manifest_corpus() -> str:
    result = _checked([sys.executable, str(CORPUS_VERIFY), "--verify"], timeout=180)
    return result.stdout + result.stderr


def _load_golden_ids(path: Path) -> list[str]:
    from eval.run_eval import load_golden

    try:
        questions = load_golden(path, require_assignment_counts=path.resolve() == DEFAULT_GOLDEN)
    except (OSError, ValueError) as exc:
        raise ExperimentError(f"golden set validation failed: {exc}") from exc
    return [question["id"] for question in questions]


def _validate_baseline(path: Path) -> str:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read existing baseline {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ExperimentError("existing baseline must contain a JSON object")
    return _file_sha256(path)


def _read_image_declarations() -> dict[str, str]:
    image_names: dict[str, str] = {}
    for path in sorted(EXPERIMENT_DIR.glob("*.yaml")):
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if not isinstance(document, dict):
                continue
            name = document.get("metadata", {}).get("name", "")
            spec = document.get("spec", {})
            pod_spec = spec.get("template", {}).get("spec", {})
            containers = pod_spec.get("containers", [])
            if not containers:
                continue
            role = (
                "api"
                if name == "chunk-api"
                else "indexer"
                if name == "indexer-profile-template"
                else "chroma"
                if name == "chunk-chroma"
                else "redis"
                if name == "chunk-redis"
                else None
            )
            if role:
                image_names[role] = containers[0]["image"]
    expected_roles = {"api", "indexer", "chroma", "redis"}
    if set(image_names) != expected_roles:
        raise ExperimentError(
            f"could not read all experiment image identifiers from manifests: {image_names}"
        )
    return image_names


def _source_fingerprints() -> dict[str, str]:
    sources = {
        DEFAULT_PROFILES,
        DEFAULT_GOLDEN,
        DEFAULT_BASELINE,
        EVALUATOR,
        Path(__file__).resolve(),
        *EXPERIMENT_DIR.glob("*.yaml"),
    }
    return {
        path.resolve().relative_to(ROOT).as_posix(): _file_sha256(path)
        for path in sorted(sources, key=lambda source: source.as_posix())
    }


def _check_namespace_is_new() -> None:
    result = _checked(
        ["kubectl", "get", "namespace", NAMESPACE, "-o", "name", "--ignore-not-found=true"]
    )
    if result.stdout.strip():
        raise ExperimentError(
            f"namespace {NAMESPACE!r} already exists; refusing to reuse or modify it. "
            "Inspect and clean up only the experiment namespace before retrying."
        )


def _pod_image_ids(selector: str) -> list[dict[str, Any]]:
    result = _checked(
        ["kubectl", "get", "pods", "-n", NAMESPACE, "-l", selector, "-o", "json"]
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ExperimentError(f"kubectl returned malformed pod JSON for {selector}") from exc
    pods = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(pods, list) or not pods:
        raise ExperimentError(f"no pods found for image selector {selector!r}")

    observed: list[dict[str, Any]] = []
    for pod in pods:
        metadata = pod.get("metadata", {})
        statuses = pod.get("status", {}).get("containerStatuses", [])
        if not statuses:
            continue
        observed.append(
            {
                "pod": metadata.get("name"),
                "containers": [
                    {
                        "name": item.get("name"),
                        "image": item.get("image"),
                        "image_id": item.get("imageID"),
                    }
                    for item in statuses
                ],
            }
        )
    if not observed:
        raise ExperimentError(f"pods for {selector!r} have no container image identifiers")
    return observed


def _ollama_tags() -> dict[str, Any]:
    script = (
        "import json, os, sys, httpx\n"
        "url = os.environ['OLLAMA_URL'].rstrip('/') + '/api/tags'\n"
        "response = httpx.get(url, timeout=10.0)\n"
        "response.raise_for_status()\n"
        "payload = response.json()\n"
        "models = payload.get('models') if isinstance(payload, dict) else None\n"
        "if not isinstance(models, list): raise RuntimeError('invalid Ollama tags response')\n"
        "expected = {'EMBED_MODEL': os.environ['EMBED_MODEL'], "
        "'LLM_MODEL': os.environ['LLM_MODEL']}\n"
        "def canonical(value):\n"
        "    name = value.rsplit('/', 1)[-1]\n"
        "    return name if ':' in name else name + ':latest'\n"
        "found = {}\n"
        "for key, wanted in expected.items():\n"
        "    model = next((item for item in models if isinstance(item, dict) and "
        "canonical(str(item.get('name', item.get('model', '')))) == canonical(wanted)), None)\n"
        "    if model is None: raise RuntimeError('configured model tag missing: ' + wanted)\n"
        "    found[key] = {'configured': wanted, 'name': model.get('name', model.get('model')), "
        "'digest': model.get('digest'), 'modified_at': model.get('modified_at')}\n"
        "print(json.dumps(found))\n"
    )
    result = _checked(
        [
            "kubectl",
            "exec",
            "-n",
            NAMESPACE,
            "deployment/chunk-api",
            "--",
            "python",
            "-c",
            script,
        ],
        timeout=30,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ExperimentError("API pod returned malformed Ollama tag metadata") from exc
    if not isinstance(payload, dict) or set(payload) != {"EMBED_MODEL", "LLM_MODEL"}:
        raise ExperimentError("API pod returned incomplete Ollama model tags")
    return payload


def _query_collection(collection: str) -> dict[str, Any]:
    base_url = f"http://127.0.0.1:{CHROMA_LOCAL_PORT}"
    collections_url = (
        f"{base_url}/api/v2/tenants/default_tenant/databases/default_database/"
        "collections"
    )
    try:
        response = httpx.get(collections_url, timeout=CHROMA_QUERY_TIMEOUT)
        if response.status_code != 200:
            raise ExperimentError(
                f"Chroma collection list returned HTTP {response.status_code}"
            )
        collection_list = response.json()
        if not isinstance(collection_list, list) or any(
            not isinstance(item, dict) for item in collection_list
        ):
            raise ExperimentError("Chroma returned an invalid collection list")

        collection_info = next(
            (item for item in collection_list if item.get("name") == collection),
            None,
        )
        if collection_info is None:
            return {"exists": False}

        collection_id = collection_info.get("id")
        metadata = collection_info.get("metadata")
        if (
            not isinstance(collection_id, str)
            or not collection_id.strip()
            or (metadata is not None and not isinstance(metadata, dict))
        ):
            raise ExperimentError(
                f"Chroma returned invalid collection metadata for {collection}"
            )
        collection_id = collection_id.strip()

        count_url = f"{collections_url}/{quote(collection_id, safe='')}/count"
        count_response = httpx.get(count_url, timeout=CHROMA_QUERY_TIMEOUT)
        if count_response.status_code != 200:
            raise ExperimentError(
                f"Chroma collection count returned HTTP {count_response.status_code}"
            )
        count = count_response.json()
    except httpx.HTTPError as exc:
        raise ExperimentError(
            f"Chroma collection inspection failed ({type(exc).__name__})"
        ) from exc
    except ValueError as exc:
        raise ExperimentError(
            f"Chroma returned malformed collection state for {collection}"
        ) from exc

    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ExperimentError(f"Chroma returned an invalid record count for {collection}")
    return {"exists": True, "count": count, "metadata": metadata or {}}


def _wait_for_api_shutdown(timeout: int = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _checked(
            ["kubectl", "get", "pods", "-n", NAMESPACE, "-l", "app=chunk-api", "-o", "json"]
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ExperimentError("kubectl returned malformed API pod state while scaling down") from exc
        pods = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(pods, list):
            raise ExperimentError("kubectl returned invalid API pod state while scaling down")
        if not pods:
            return
        time.sleep(1)
    raise ExperimentError("API pod did not terminate before the next profile could start")


def _profile_config_map(profile: ChunkProfile, run_id: str) -> tuple[str, dict[str, Any]]:
    collection = collection_name(run_id, profile)
    name = f"chunk-profile-{run_id}-{profile.name}"
    document = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": name,
            "namespace": NAMESPACE,
            "labels": {"rahbar.experiment": "chunk-tuning", "rahbar.profile": profile.name},
        },
        "data": {
            "CHROMA_COLLECTION": collection,
            "CHUNK_SIZE": str(profile.chunk_size),
            "CHUNK_OVERLAP": str(profile.chunk_overlap),
        },
    }
    return name, document


def _profile_job(profile: ChunkProfile, run_id: str, config_map: str) -> dict[str, Any]:
    template = _read_yaml(EXPERIMENT_DIR / "indexer-job-template.yaml")
    if not isinstance(template, dict):
        raise ExperimentError("indexer Job template must contain a YAML object")
    job = deepcopy(template)
    job["metadata"]["name"] = f"indexer-{run_id}-{profile.name}"
    job["metadata"]["labels"] = {
        "rahbar.experiment": "chunk-tuning",
        "rahbar.profile": profile.name,
    }
    refs = job["spec"]["template"]["spec"]["containers"][0]["envFrom"]
    profile_ref = next(
        (item for item in refs if item["configMapRef"]["name"] == "profile-config-template"),
        None,
    )
    if profile_ref is None:
        raise ExperimentError("indexer Job template is missing its profile ConfigMap reference")
    profile_ref["configMapRef"]["name"] = config_map
    return job


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _write_indexer_logs(profile_dir: Path, job: str, wait_error: ExperimentError | None) -> None:
    result = _run(
        ["kubectl", "logs", "-n", NAMESPACE, f"job/{job}", "--all-containers=true"],
        timeout=180,
    )
    text = result.stdout + result.stderr
    if wait_error:
        text = f"Job wait failed: {wait_error}\n\n{text}"
    (profile_dir / "indexing.log").write_text(text, encoding="utf-8")
    if result.returncode:
        raise ExperimentError(
            f"could not retrieve indexing logs for {job}: {(result.stderr or result.stdout).strip()}"
        )
    if wait_error:
        raise wait_error


def _run_indexer(profile_dir: Path, job_name: str) -> None:
    wait_error: ExperimentError | None = None
    try:
        _checked(
            [
                "kubectl",
                "wait",
                "-n",
                NAMESPACE,
                "--for=condition=complete",
                f"job/{job_name}",
                f"--timeout={JOB_TIMEOUT_SECONDS}s",
            ],
            timeout=JOB_TIMEOUT_SECONDS + 30,
        )
    except ExperimentError as exc:
        wait_error = exc
    _write_indexer_logs(profile_dir, job_name, wait_error)


def _start_port_forward(
    profile_dir: Path,
    local_port: int,
    *,
    service: str = API_SERVICE,
    probe_path: str = "/healthz",
) -> tuple[subprocess.Popen[bytes], Any]:
    log_path = profile_dir / "port-forward.log"
    stream = log_path.open("wb")
    try:
        process = subprocess.Popen(
            [
                "kubectl",
                "port-forward",
                "-n",
                NAMESPACE,
                f"service/{service}",
                f"{local_port}:8000",
                "--address=127.0.0.1",
            ],
            cwd=ROOT,
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
    except (FileNotFoundError, OSError) as exc:
        stream.close()
        raise ExperimentError(f"cannot start kubectl port-forward: {exc}") from exc

    deadline = time.monotonic() + PORT_FORWARD_TIMEOUT
    health_url = f"http://127.0.0.1:{local_port}{probe_path}"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stream.close()
            raise ExperimentError(
                f"kubectl port-forward exited with status {process.returncode}; "
                f"see {log_path}"
            )
        try:
            response = httpx.get(health_url, timeout=1.0)
            if response.status_code == 200:
                return process, stream
        except httpx.HTTPError:
            pass
        time.sleep(0.5)

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    stream.close()
    raise ExperimentError(
        f"service {service!r} did not become available at {health_url}; see {log_path}"
    )


def _stop_port_forward(process: subprocess.Popen[bytes], stream: Any) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    stream.close()


def _validate_complete_evaluation(
    payload: Any,
    expected_ids: list[str],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ExperimentError("evaluation output must be a JSON object")
    questions = payload.get("questions")
    metrics = payload.get("metrics")
    gate = payload.get("gate")
    if (
        not isinstance(questions, list)
        or not isinstance(metrics, dict)
        or not isinstance(gate, dict)
        or not isinstance(gate.get("passed"), bool)
        or not isinstance(gate.get("failures"), list)
    ):
        raise ExperimentError(
            "evaluation output is missing question rows, aggregate metrics, or gate details"
        )
    actual_ids = [row.get("id") for row in questions if isinstance(row, dict)]
    if actual_ids != expected_ids:
        raise ExperimentError("evaluation output does not contain the exact golden question set")
    if metrics.get("successful_requests") != len(expected_ids) or metrics.get(
        "request_failures"
    ) != 0:
        failures = metrics.get("request_failure_details", [])
        raise ExperimentError(
            "evaluation requests were incomplete; refusing to include this profile "
            f"in the comparison (request failures: {failures})"
        )
    return payload


def _question_failures(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": row["id"],
            "request_status": row.get("request_status"),
            "reasons": row.get("pass_reasons", []),
        }
        for row in payload["questions"]
        if not row.get("passed", False)
    ]


def _comparison_row(
    profile: ChunkProfile,
    collection_count: int,
    payload: dict[str, Any],
    failures: list[dict[str, Any]],
) -> dict[str, Any]:
    metrics = payload["metrics"]
    return {
        "profile": profile.name,
        "chunk_size": profile.chunk_size,
        "chunk_overlap": profile.chunk_overlap,
        "chunk_count": collection_count,
        "recall_at_3": metrics.get("recall_at_3"),
        "recall_at_5": metrics.get("recall_at_5"),
        "citation_provenance_validity_percentage": metrics.get(
            "citation_provenance_validity_percentage"
        ),
        "gold_evidence_relevance_percentage": metrics.get(
            "gold_evidence_relevance_percentage"
        ),
        "must_abstain_correctness_percentage": metrics.get(
            "must_abstain_correctness_percentage"
        ),
        "evaluation_gate_passed": payload["gate"]["passed"],
        "evaluation_gate_failures": payload["gate"]["failures"],
        "failed_question_ids": [item["id"] for item in failures],
    }


def _write_comparison_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "# RAHBAR chunk tuning comparison",
        "",
        "| Profile | Size | Overlap | Chunks | Recall@3 | Recall@5 | Citation provenance | "
        "Gold relevance | Must-abstain | Gate | Failed question IDs |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        def display(value: Any) -> str:
            return "unavailable" if value is None else f"{value:.4f}"

        def display_percentage(value: Any) -> str:
            return "unavailable" if value is None else f"{value:.1f}%"

        lines.append(
            "| {profile} | {chunk_size} | {chunk_overlap} | {chunk_count} | {recall3} | "
            "{recall5} | {provenance} | {relevance} | {abstain} | {gate} | {failed} |".format(
                profile=row["profile"],
                chunk_size=row["chunk_size"],
                chunk_overlap=row["chunk_overlap"],
                chunk_count=row["chunk_count"],
                recall3=display(row["recall_at_3"]),
                recall5=display(row["recall_at_5"]),
                provenance=display_percentage(
                    row["citation_provenance_validity_percentage"]
                ),
                relevance=display_percentage(row["gold_evidence_relevance_percentage"]),
                abstain=display_percentage(row["must_abstain_correctness_percentage"]),
                gate="pass" if row["evaluation_gate_passed"] else "fail",
                failed=", ".join(row["failed_question_ids"]) or "none",
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_profile_config(
    profile_dir: Path,
    profile: ChunkProfile,
    collection: str,
    common: dict[str, str],
    run_metadata: dict[str, Any],
    golden_hash: str,
    baseline_hash: str,
) -> None:
    _write_json(
        profile_dir / "configuration.json",
        {
            "profile": asdict(profile),
            "collection": collection,
            "cache_scope": cache_scope(common["CACHE_PREFIX"], collection),
            "common_settings": common,
            "golden_sha256": golden_hash,
            "baseline_sha256": baseline_hash,
            "git_commit": run_metadata["git_commit"],
            "declared_image_identifiers": run_metadata["declared_image_identifiers"],
            "ollama_model_tags": run_metadata.get("ollama_model_tags"),
        },
    )


def _evaluate_profile(
    profile_dir: Path,
    api_host: str,
    local_port: int,
    golden_path: Path,
    baseline_path: Path,
) -> tuple[dict[str, Any], str]:
    process, stream = _start_port_forward(profile_dir, local_port)
    command = [
        sys.executable,
        str(EVALUATOR),
        "--api",
        f"http://127.0.0.1:{local_port}",
        "--host",
        api_host,
        "--golden",
        str(golden_path),
        "--out",
        str(profile_dir / "evaluation.json"),
        "--baseline",
        str(baseline_path),
        "--timeout",
        str(EVALUATOR_REQUEST_TIMEOUT_SECONDS),
    ]
    try:
        result = _run(command, timeout=EVALUATION_TIMEOUT)
    except ExperimentError as exc:
        (profile_dir / "evaluation.log").write_text(str(exc) + "\n", encoding="utf-8")
        raise
    finally:
        _stop_port_forward(process, stream)

    output = result.stdout + result.stderr
    (profile_dir / "evaluation.log").write_text(output, encoding="utf-8")
    if result.returncode not in {0, 1}:
        raise ExperimentError(
            f"evaluation runner failed with exit code {result.returncode}; "
            f"see {profile_dir / 'evaluation.log'}"
        )
    try:
        payload = json.loads((profile_dir / "evaluation.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"evaluation did not write valid JSON: {exc}") from exc
    expected_ids = _load_golden_ids(golden_path)
    try:
        return _validate_complete_evaluation(payload, expected_ids), output
    except ExperimentError:
        metrics = payload.get("metrics", {})
        questions = payload.get("questions", [])
        failed_questions = []
        if isinstance(questions, list):
            failed_questions = [
                {
                    "id": row.get("id"),
                    "request_status": row.get("request_status"),
                    "reasons": row.get("pass_reasons", []),
                }
                for row in questions
                if isinstance(row, dict)
                and (
                    not row.get("passed", False)
                    or row.get("request_status") == "failed"
                )
            ]
        _write_json(
            profile_dir / "failures.json",
            {
                "failed_questions": failed_questions,
                "request_failure_details": (
                    metrics.get("request_failure_details", [])
                    if isinstance(metrics, dict)
                    else []
                ),
            },
        )
        raise


def run_experiment(
    *,
    output_dir: Path,
    profile_path: Path = DEFAULT_PROFILES,
    golden_path: Path = DEFAULT_GOLDEN,
    baseline_path: Path = DEFAULT_BASELINE,
    local_port: int = 18080,
    api_host: str = "rahbar.local",
) -> Path:
    validate_output_path(output_dir)
    if profile_path.resolve() != DEFAULT_PROFILES.resolve():
        raise ExperimentError("the tuning run must use the approved eval/chunk-tuning.yaml profiles")
    if golden_path.resolve() != DEFAULT_GOLDEN.resolve():
        raise ExperimentError("the tuning run must use the existing eval/golden.yaml set")
    if baseline_path.resolve() != DEFAULT_BASELINE.resolve():
        raise ExperimentError("the tuning run must preserve and read eval/baseline.json")
    profiles = load_profiles(profile_path)
    common = load_common_settings(EXPERIMENT_DIR / "configmap.yaml")
    golden_ids = _load_golden_ids(golden_path)
    if len(golden_ids) != 15:
        raise ExperimentError(f"expected 15 golden questions, found {len(golden_ids)}")
    golden_hash = _file_sha256(golden_path)
    baseline_hash = _validate_baseline(baseline_path)
    run_id = output_dir.name
    if not _RUN_ID.fullmatch(run_id):
        raise ExperimentError("output directory name must be a 1-21 character lowercase run ID")
    collections = [collection_name(run_id, profile) for profile in profiles]
    if len(set(collections)) != len(collections):
        raise ExperimentError("profile collection names are not unique")
    if not 1024 <= local_port <= 65535 or local_port == CHROMA_LOCAL_PORT:
        raise ExperimentError(
            f"--port must be between 1024 and 65535 and differ from Chroma's "
            f"port {CHROMA_LOCAL_PORT}"
        )

    initial_corpus = corpus_snapshot(CORPUS_DIR)
    corpus_manifest_log = _verify_manifest_corpus()
    commit = _git_commit()
    image_declarations = _read_image_declarations()
    _check_namespace_is_new()

    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ExperimentError(f"output path already exists; refusing to overwrite: {output_dir}") from exc

    run_metadata: dict[str, Any] = {
        "run_id": run_id,
        "namespace": NAMESPACE,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "in_progress",
        "git_commit": commit,
        "declared_image_identifiers": image_declarations,
        "corpus": initial_corpus,
        "corpus_verification": "corpus/fetch.py --verify",
        "corpus_verification_log": corpus_manifest_log,
        "golden_path": str(golden_path.resolve()),
        "golden_sha256": golden_hash,
        "baseline_path": str(baseline_path.resolve()),
        "baseline_sha256": baseline_hash,
        "source_fingerprints": _source_fingerprints(),
        "locked_settings": common,
        "profiles": [asdict(item) for item in profiles],
    }
    _write_json(output_dir / "run.json", run_metadata)
    (output_dir / "corpus-verification.log").write_text(
        corpus_manifest_log, encoding="utf-8"
    )

    current_profile_dir: Path | None = None
    comparison_rows: list[dict[str, Any]] = []
    ollama_tags: dict[str, Any] | None = None
    chroma_forward: tuple[subprocess.Popen[bytes], Any] | None = None
    try:
        _checked(["kubectl", "create", "-f", str(NAMESPACE_MANIFEST)], timeout=60)
        _checked(["kubectl", "apply", "-k", str(EXPERIMENT_DIR)], timeout=180)
        _checked(
            [
                "kubectl",
                "rollout",
                "status",
                "-n",
                NAMESPACE,
                "statefulset/chunk-chroma",
                "--timeout=240s",
            ],
            timeout=270,
        )
        _checked(
            [
                "kubectl",
                "rollout",
                "status",
                "-n",
                NAMESPACE,
                "deployment/chunk-redis",
                "--timeout=240s",
            ],
            timeout=270,
        )
        chroma_forward = _start_port_forward(
            output_dir,
            CHROMA_LOCAL_PORT,
            service=CHROMA_SERVICE,
            probe_path="/api/v2/heartbeat",
        )

        for profile in profiles:
            current_profile_dir = output_dir / profile.name
            current_profile_dir.mkdir(exist_ok=False)
            collection = collection_name(run_id, profile)
            config_map_name, config_map = _profile_config_map(profile, run_id)
            job_name = f"indexer-{run_id}-{profile.name}"
            job = _profile_job(profile, run_id, config_map_name)
            _write_profile_config(
                current_profile_dir,
                profile,
                collection,
                common,
                run_metadata,
                golden_hash,
                baseline_hash,
            )
            _write_yaml(current_profile_dir / "profile-configmap.yaml", config_map)
            _write_yaml(current_profile_dir / "indexer-job.yaml", job)
            (current_profile_dir / "indexing.log").write_text(
                "Indexer Job has not started.\n", encoding="utf-8"
            )
            _write_json(current_profile_dir / "collection.json", {"status": "not_indexed"})
            _write_json(current_profile_dir / "failures.json", {"failed_questions": []})

            existing = _query_collection(collection)
            if existing["exists"]:
                raise ExperimentError(
                    f"collection {collection!r} already exists; refusing to reuse its data"
                )
            if _file_sha256(golden_path) != golden_hash:
                raise ExperimentError("golden set changed during the experiment")

            before = corpus_snapshot(CORPUS_DIR)
            verification = _verify_manifest_corpus()
            (current_profile_dir / "corpus-verification.log").write_text(
                verification, encoding="utf-8"
            )
            if before["sha256"] != initial_corpus["sha256"]:
                raise ExperimentError("corpus changed after the initial checksum verification")

            _checked(
                [
                    "kubectl",
                    "apply",
                    "-f",
                    str(current_profile_dir / "profile-configmap.yaml"),
                ]
            )
            _checked(
                [
                    "kubectl",
                    "apply",
                    "-f",
                    str(current_profile_dir / "indexer-job.yaml"),
                ]
            )
            _run_indexer(current_profile_dir, job_name)
            after = corpus_snapshot(CORPUS_DIR)
            if after["sha256"] != initial_corpus["sha256"]:
                raise ExperimentError("corpus changed while the indexer Job was running")

            collection_info = _query_collection(collection)
            if not collection_info["exists"]:
                raise ExperimentError(f"indexer completed but collection {collection!r} is missing")
            count = collection_info.get("count")
            metadata = collection_info.get("metadata")
            if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                raise ExperimentError(f"collection {collection!r} is empty or has invalid count")
            if not isinstance(metadata, dict) or metadata.get("embed_model") != common["EMBED_MODEL"]:
                raise ExperimentError(
                    f"collection {collection!r} metadata does not match the configured embedding model"
                )
            _write_json(
                current_profile_dir / "collection.json",
                {
                    "name": collection,
                    "count": count,
                    "metadata": metadata,
                    "corpus_sha256": after["sha256"],
                },
            )
            indexing_images = _pod_image_ids(f"job-name={job_name}")

            _checked(
                ["kubectl", "scale", "-n", NAMESPACE, "deployment/chunk-api", "--replicas=0"]
            )
            _checked(
                [
                    "kubectl",
                    "set",
                    "env",
                    "-n",
                    NAMESPACE,
                    "deployment/chunk-api",
                    f"CHROMA_COLLECTION={collection}",
                ]
            )
            _checked(
                ["kubectl", "scale", "-n", NAMESPACE, "deployment/chunk-api", "--replicas=1"]
            )
            _checked(
                [
                    "kubectl",
                    "rollout",
                    "status",
                    "-n",
                    NAMESPACE,
                    "deployment/chunk-api",
                    f"--timeout={API_ROLLOUT_TIMEOUT}s",
                ],
                timeout=API_ROLLOUT_TIMEOUT + 30,
            )

            current_tags = _ollama_tags()
            if ollama_tags is None:
                ollama_tags = current_tags
                run_metadata["ollama_model_tags"] = ollama_tags
            elif current_tags != ollama_tags:
                raise ExperimentError("Ollama model tags changed during the sequential experiment")
            _write_profile_config(
                current_profile_dir,
                profile,
                collection,
                common,
                run_metadata,
                golden_hash,
                baseline_hash,
            )

            api_images = _pod_image_ids("app=chunk-api")
            infrastructure_images = {
                "chroma": _pod_image_ids("app=chunk-chroma"),
                "redis": _pod_image_ids("app=chunk-redis"),
                "api": api_images,
                "indexer": indexing_images,
            }
            _write_json(
                current_profile_dir / "image-identifiers.json",
                {
                    "declared": image_declarations,
                    "observed": infrastructure_images,
                    "ollama_model_tags": ollama_tags,
                },
            )

            if _file_sha256(golden_path) != golden_hash:
                raise ExperimentError("golden set changed before evaluation")
            evaluation, evaluation_log = _evaluate_profile(
                current_profile_dir,
                api_host,
                local_port,
                golden_path.resolve(),
                baseline_path.resolve(),
            )
            del evaluation_log
            failures = _question_failures(evaluation)
            _write_json(
                current_profile_dir / "failures.json",
                {"failed_questions": failures},
            )
            _write_json(current_profile_dir / "run-evaluation-metrics.json", evaluation["metrics"])
            if _file_sha256(golden_path) != golden_hash:
                raise ExperimentError("golden set changed during evaluation")
            if _file_sha256(baseline_path) != baseline_hash:
                raise ExperimentError("baseline changed during evaluation; refusing to continue")
            comparison_rows.append(_comparison_row(profile, count, evaluation, failures))
            _checked(
                ["kubectl", "scale", "-n", NAMESPACE, "deployment/chunk-api", "--replicas=0"]
            )
            _wait_for_api_shutdown()

        if len(comparison_rows) != len(profiles):
            raise ExperimentError("one or more profile evaluations did not complete")
        comparison = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "git_commit": commit,
            "corpus_sha256": initial_corpus["sha256"],
            "golden_sha256": golden_hash,
            "baseline_sha256": baseline_hash,
            "ollama_model_tags": ollama_tags,
            "profiles": comparison_rows,
        }
        _write_json(output_dir / "comparison.json", comparison)
        _write_comparison_markdown(output_dir / "comparison.md", comparison_rows)
        gate_failures = [
            row["profile"]
            for row in comparison_rows
            if not row["evaluation_gate_passed"]
        ]
        run_metadata["status"] = (
            "completed_with_evaluation_failures" if gate_failures else "completed"
        )
        run_metadata["evaluation_gate_failed_profiles"] = gate_failures
        run_metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
        run_metadata["ollama_model_tags"] = ollama_tags
        run_metadata["comparison"] = "comparison.json"
        _write_json(output_dir / "run.json", run_metadata)
    except Exception as exc:
        if current_profile_dir is not None:
            failure_path = current_profile_dir / "failures.json"
            try:
                existing_failure = json.loads(failure_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing_failure = {"failed_questions": []}
            existing_failure["runner_error"] = f"{type(exc).__name__}: {exc}"
            _write_json(failure_path, existing_failure)
        run_metadata["status"] = "failed"
        run_metadata["failed_at"] = datetime.now(timezone.utc).isoformat()
        run_metadata["error"] = f"{type(exc).__name__}: {exc}"
        _write_json(output_dir / "run.json", run_metadata)
        raise
    finally:
        if chroma_forward is not None:
            _stop_port_forward(*chroma_forward)
    return output_dir


def _arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and evaluate three isolated RAHBAR chunk configurations on Minikube."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="new run directory (default: eval/chunk-tuning-runs/<unique-run-id>)",
    )
    parser.add_argument("--port", type=int, default=18080, help="local API port-forward port")
    parser.add_argument("--host", default="rahbar.local", help="Host header for run_eval.py")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _arg_parser().parse_args(argv)
    output_dir = args.output_dir
    if output_dir is None:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M") + f"-{os.urandom(3).hex()}"
        output_dir = DEFAULT_OUTPUT_ROOT / run_id
    elif not output_dir.is_absolute():
        output_dir = ROOT / output_dir

    try:
        result_dir = run_experiment(
            output_dir=output_dir,
            local_port=args.port,
            api_host=args.host,
        )
    except (ExperimentError, OSError) as exc:
        print(f"chunk tuning failed: {exc}", file=sys.stderr)
        return 1

    print(f"Chunk tuning completed. Results: {result_dir}")
    print(f"Comparison: {result_dir / 'comparison.md'}")
    try:
        comparison = json.loads((result_dir / "comparison.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("chunk tuning completed but the comparison summary could not be read", file=sys.stderr)
        return 1
    failed_profiles = [
        profile["profile"]
        for profile in comparison.get("profiles", [])
        if not profile.get("evaluation_gate_passed", False)
    ]
    if failed_profiles:
        print(
            "Evaluation gate failed for profile(s): "
            + ", ".join(failed_profiles)
            + ". Complete measurements are preserved in the run directory.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
