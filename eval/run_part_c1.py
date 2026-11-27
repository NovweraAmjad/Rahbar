#!/usr/bin/env python3
"""Run Assignment 03 Part C1 against isolated baseline and candidate indexes."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "api") not in sys.path:
    sys.path.insert(0, str(ROOT / "api"))

from eval import run_chunk_tuning as tuning  # noqa: E402
from eval import run_part_b_suite as part_b  # noqa: E402


HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "part-c1.yaml"
DEFAULT_GOLDEN = HERE / "golden.jsonl"
DEFAULT_OUTPUT_ROOT = HERE / "results"
CORPUS_ROOT = ROOT / "corpus"
EXPERIMENT_SOURCE = ROOT / "k8s" / "experiments" / "chunk-tuning"
EXPERIMENT_FILES = (
    "namespace.yaml",
    "serviceaccounts.yaml",
    "configmap.yaml",
    "indexer-data-pvc.yaml",
    "chroma.yaml",
    "redis.yaml",
    "api.yaml",
    "indexer-job-template.yaml",
    "kustomization.yaml",
)
RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")


class PartC1Error(RuntimeError):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def load_config(path: Path) -> tuple[dict[str, Any], dict[str, str], list[tuning.ChunkProfile]]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PartC1Error(f"cannot read C1 configuration {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise PartC1Error("C1 configuration must have schema_version 1")
    change = raw.get("change")
    constants = raw.get("held_constant")
    if (
        raw.get("experiment") != "chunk-size"
        or not isinstance(change, dict)
        or change.get("variable") != "chunk_size"
        or change.get("unit") != "whitespace_tokens"
        or not isinstance(constants, dict)
        or any(not isinstance(key, str) or not isinstance(value, str) for key, value in constants.items())
    ):
        raise PartC1Error("C1 configuration must define the chunk_size change and string constants")

    profiles: list[tuning.ChunkProfile] = []
    for role in ("baseline", "candidate"):
        item = change.get(role)
        if not isinstance(item, dict):
            raise PartC1Error(f"C1 configuration is missing the {role} profile")
        name = item.get("name")
        size = item.get("chunk_size")
        overlap = item.get("chunk_overlap")
        if name != role:
            raise PartC1Error(f"the {role} profile must be named {role!r}")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise PartC1Error(f"{role}: chunk_size must be a positive integer")
        if isinstance(overlap, bool) or not isinstance(overlap, int) or overlap < 0 or overlap >= size:
            raise PartC1Error(f"{role}: chunk_overlap must be non-negative and smaller than chunk_size")
        profiles.append(tuning.ChunkProfile(role, size, overlap))

    if profiles[0].chunk_size == profiles[1].chunk_size:
        raise PartC1Error("baseline and candidate chunk_size must differ")
    if profiles[0].chunk_overlap != profiles[1].chunk_overlap:
        raise PartC1Error("chunk_overlap must remain constant for a chunk_size-only experiment")
    return raw, dict(constants), profiles


def common_settings(constants: dict[str, str]) -> dict[str, str]:
    common = tuning.load_common_settings(EXPERIMENT_SOURCE / "configmap.yaml")
    for key, value in constants.items():
        if key not in common or common[key] != value:
            raise PartC1Error(f"C1 held-constant value differs from experiment config: {key}")
    return {
        **common,
        "QUARANTINE_DIR": "/tmp/quarantine",
        "VERIFICATION_REPORT_DIR": "/tmp/verification-reports",
        "ARCHIVE_DIR": "/tmp/archive",
    }


def corpus_snapshot(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        raise PartC1Error(f"corpus root is missing: {root}")
    indexes = sorted(root.rglob("_index.json"))
    if not indexes:
        raise PartC1Error(f"no corpus indexes found below {root}")

    files: dict[str, str] = {}
    for index_path in indexes:
        try:
            entries = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PartC1Error(f"cannot read corpus index {index_path}: {exc}") from exc
        if not isinstance(entries, list) or not entries:
            raise PartC1Error(f"corpus index is empty or invalid: {index_path}")
        relative_index = index_path.relative_to(root).as_posix()
        files[relative_index] = file_sha256(index_path)
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
                raise PartC1Error(f"invalid entry in corpus index {index_path}")
            document_path = index_path.parent / entry["file"]
            if (
                not document_path.is_file()
                or document_path.resolve().parent != index_path.parent.resolve()
            ):
                raise PartC1Error(f"unsafe or missing corpus file in {index_path}: {entry['file']}")
            actual = file_sha256(document_path)
            expected = entry.get("sha256")
            if expected != actual:
                raise PartC1Error(f"corpus SHA-256 mismatch for {document_path}")
            files[document_path.relative_to(root).as_posix()] = actual

    digest = hashlib.sha256()
    for name, checksum in sorted(files.items()):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(checksum.encode("ascii"))
        digest.update(b"\n")
    return {
        "sha256": digest.hexdigest(),
        "document_count": len(files) - len(indexes),
        "files": files,
    }


def replace_namespace(
    source_dir: Path,
    target_dir: Path,
    namespace: str,
    common: dict[str, str],
    corpus_node_path: str,
) -> None:
    target_dir.mkdir(parents=True, exist_ok=False)
    for filename in EXPERIMENT_FILES:
        source = source_dir / filename
        try:
            documents = list(yaml.safe_load_all(source.read_text(encoding="utf-8")))
        except (OSError, yaml.YAMLError) as exc:
            raise PartC1Error(f"cannot read experiment manifest {source}: {exc}") from exc
        transformed: list[dict[str, Any]] = []
        for raw in documents:
            if not isinstance(raw, dict):
                raise PartC1Error(f"manifest {source} contains a non-object document")
            document = deepcopy(raw)
            metadata = document.setdefault("metadata", {})
            if metadata.get("namespace") == "rahbar-chunk-tuning":
                metadata["namespace"] = namespace
            if document.get("kind") == "Namespace":
                metadata["name"] = namespace
            if document.get("kind") == "ConfigMap" and metadata.get("name") == "chunk-tuning-common":
                document["data"] = common
            if document.get("kind") == "Job":
                pod_spec = document.get("spec", {}).get("template", {}).get("spec", {})
                for volume in pod_spec.get("volumes", []):
                    host_path = volume.get("hostPath")
                    if isinstance(host_path, dict):
                        host_path["path"] = corpus_node_path
            transformed.append(document)
        rendered = yaml.safe_dump_all(transformed, sort_keys=False)
        (target_dir / filename).write_text(rendered, encoding="utf-8")


def validate_suite(summary_path: Path, expected_questions: int) -> dict[str, Any]:
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PartC1Error(f"Part B suite did not write valid JSON: {exc}") from exc
    runs = summary.get("runs") if isinstance(summary, dict) else None
    if not isinstance(runs, dict) or set(runs) != {"1", "2"}:
        raise PartC1Error("Part B suite must contain exactly runs 1 and 2")
    metadata = summary.get("metadata")
    if not isinstance(metadata, dict):
        raise PartC1Error("Part B suite is missing runtime metadata")
    try:
        part_b.validate_api_image_reference(metadata.get("api_image_reference", ""))
    except ValueError as exc:
        raise PartC1Error("Part B suite did not record an immutable API image") from exc
    for number in ("1", "2"):
        run = runs[number]
        if not isinstance(run, dict) or set(run) != {"module", "component", "end_to_end"}:
            raise PartC1Error(f"Part B run {number} is missing an evaluation level")
        end = run["end_to_end"]
        if (
            end.get("successful_requests") != expected_questions
            or end.get("request_failures") != 0
        ):
            raise PartC1Error(f"Part B run {number} had incomplete end-to-end requests")
        component = run["component"]
        if any(component.get(name) is None for name in (
            "baseline_recall_at_3",
            "baseline_recall_at_5",
            "baseline_mrr",
            "baseline_ndcg_at_5",
            "typo_recall_at_3",
            "typo_recall_at_5",
            "typo_mrr",
            "typo_ndcg_at_5",
        )):
            raise PartC1Error(f"Part B run {number} has unavailable component metrics")
    return summary


def api_image_reference(namespace: str) -> str:
    result = tuning._checked(
        [
            "kubectl",
            "get",
            "pods",
            "-n",
            namespace,
            "-l",
            "app=chunk-api",
            "-o",
            "json",
        ]
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PartC1Error("kubectl returned invalid API pod JSON") from exc
    pods = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(pods, list):
        raise PartC1Error("kubectl API pod response has no items list")
    references = {
        container.get("imageID")
        for pod in pods
        if isinstance(pod, dict)
        and pod.get("status", {}).get("phase") == "Running"
        for container in pod.get("status", {}).get("containerStatuses", [])
        if isinstance(container, dict)
        and container.get("name") == "api"
        and container.get("ready") is True
        and isinstance(container.get("imageID"), str)
    }
    if len(references) != 1:
        raise PartC1Error(
            "expected one ready API image identity, got "
            f"{len(references)} in namespace {namespace}"
        )
    reference = references.pop()
    try:
        return part_b.validate_api_image_reference(reference)
    except ValueError as exc:
        raise PartC1Error(f"API pod image is not immutable: {reference}") from exc


def mean_run_metrics(summary: dict[str, Any]) -> dict[str, dict[str, float | int | None]]:
    combined: dict[str, dict[str, float | int | None]] = {}
    runs = summary["runs"]
    for level in ("module", "component", "end_to_end"):
        combined[level] = {}
        for name in runs["1"][level]:
            values = [runs[number][level][name] for number in ("1", "2")]
            numeric = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
            if len(numeric) != 2:
                combined[level][name] = None
            elif all(isinstance(value, int) for value in numeric):
                average = sum(numeric) / 2
                combined[level][name] = int(average) if average.is_integer() else average
            else:
                combined[level][name] = sum(numeric) / 2
    return combined


def compare_summaries(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    baseline_mean = mean_run_metrics(baseline)
    candidate_mean = mean_run_metrics(candidate)
    comparison: dict[str, Any] = {}
    for level in ("module", "component", "end_to_end"):
        comparison[level] = {}
        for metric in sorted(set(baseline_mean[level]) | set(candidate_mean[level])):
            baseline_value = baseline_mean[level].get(metric)
            candidate_value = candidate_mean[level].get(metric)
            delta = None
            relative = None
            if (
                isinstance(baseline_value, (int, float))
                and not isinstance(baseline_value, bool)
                and isinstance(candidate_value, (int, float))
                and not isinstance(candidate_value, bool)
            ):
                delta = candidate_value - baseline_value
                if baseline_value != 0:
                    relative = 100.0 * delta / abs(baseline_value)
            comparison[level][metric] = {
                "baseline_run_1": baseline["runs"]["1"][level].get(metric),
                "baseline_run_2": baseline["runs"]["2"][level].get(metric),
                "baseline_mean": baseline_value,
                "candidate_run_1": candidate["runs"]["1"][level].get(metric),
                "candidate_run_2": candidate["runs"]["2"][level].get(metric),
                "candidate_mean": candidate_value,
                "absolute_change_candidate_minus_baseline": delta,
                "relative_change_percentage": relative,
            }
    return comparison


def format_value(value: Any) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def comparison_markdown(
    comparison: dict[str, Any],
    raw: dict[str, Any],
) -> str:
    lines = [
        "# Assignment 03 Part C1 comparison",
        "",
        "Only `chunk_size` changed: baseline 400 whitespace tokens, candidate 800 "
        "whitespace tokens. `chunk_overlap` remained 50 tokens.",
        "",
        "| Level | Metric | Baseline mean | Candidate mean | Absolute change | Relative change |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for level in ("module", "component", "end_to_end"):
        for metric in sorted(comparison[level]):
            row = comparison[level][metric]
            lines.append(
                f"| {level} | {metric} | {format_value(row['baseline_mean'])} | "
                f"{format_value(row['candidate_mean'])} | "
                f"{format_value(row['absolute_change_candidate_minus_baseline'])} | "
                f"{format_value(row['relative_change_percentage'])} |"
            )
    lines.extend(
        [
            "",
            "## Isolated configuration details",
            "",
            f"- Baseline collection: `{raw['baseline']['configuration']['collection']}` "
            f"({raw['baseline']['collection']['count']} chunks).",
            f"- Candidate collection: `{raw['candidate']['configuration']['collection']}` "
            f"({raw['candidate']['collection']['count']} chunks).",
            "- The live RAHBAR deployment was not modified or rolled out.",
            "",
            "## Constants",
            "",
        ]
    )
    for key, value in sorted(raw["held_constant"].items()):
        lines.append(f"- `{key}={value}`")
    lines.extend(
        [
            "",
            "The baseline and candidate means are the arithmetic means of the two "
            "Part B stochasticity runs. Individual run files remain under each "
            "configuration directory.",
        ]
    )
    return "\n".join(lines) + "\n"


def run_experiment(
    *,
    output_dir: Path,
    config_path: Path,
    golden_path: Path,
    local_port: int,
    chroma_port: int,
    api_host: str,
    corpus_node_path: str,
) -> Path:
    tuning.validate_output_path(output_dir)
    config_document, constants, profiles = load_config(config_path)
    common = common_settings(constants)
    output_name = output_dir.name
    if not RUN_ID.fullmatch(output_name):
        raise PartC1Error("output directory name must be 1-31 lowercase letters, digits, or hyphens")
    namespace = f"rahbar-part-c1-{output_name}"
    if len(namespace) > 63:
        raise PartC1Error("output directory name produces an invalid Kubernetes namespace")
    if not 1024 <= local_port <= 65535 or not 1024 <= chroma_port <= 65535:
        raise PartC1Error("ports must be between 1024 and 65535")
    if local_port == chroma_port:
        raise PartC1Error("API and Chroma ports must differ")

    from eval.golden_schema import load_golden_jsonl

    questions = load_golden_jsonl(golden_path)
    initial_corpus = corpus_snapshot(CORPUS_ROOT)
    corpus_verification = tuning._verify_manifest_corpus()
    commit = tuning._git_commit()
    image_declarations = tuning._read_image_declarations()

    output_dir.mkdir(parents=True, exist_ok=False)
    generated_manifests = output_dir / "config" / "kubernetes"
    replace_namespace(
        EXPERIMENT_SOURCE,
        generated_manifests,
        namespace,
        common,
        corpus_node_path,
    )
    config_hash = file_sha256(config_path)
    golden_hash = file_sha256(golden_path)
    metadata = {
        "schema_version": 1,
        "experiment": "assignment-03-part-c1",
        "status": "in_progress",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "run_id": output_name,
        "namespace": namespace,
        "git_commit": commit,
        "config_path": str(config_path.resolve()),
        "config_sha256": config_hash,
        "config": config_document,
        "golden_path": str(golden_path.resolve()),
        "golden_sha256": golden_hash,
        "corpus": initial_corpus,
        "corpus_verification_log": corpus_verification,
        "held_constant": common,
        "declared_image_identifiers": image_declarations,
        "api_image_references": {},
        "live_deployment_changed": False,
    }
    write_json(output_dir / "run.json", metadata)
    (output_dir / "corpus-verification.log").write_text(corpus_verification, encoding="utf-8")
    (output_dir / "configuration.yaml").write_text(
        config_path.read_text(encoding="utf-8"), encoding="utf-8"
    )

    tuning.NAMESPACE = namespace
    tuning.EXPERIMENT_DIR = generated_manifests
    tuning.CHROMA_LOCAL_PORT = chroma_port
    chroma_forward: tuple[subprocess.Popen[bytes], Any] | None = None
    measured: dict[str, Any] = {}
    try:
        tuning._checked(["kubectl", "apply", "-k", str(generated_manifests)], timeout=180)
        tuning._checked(
            [
                "kubectl", "rollout", "status", "-n", namespace,
                "statefulset/chunk-chroma", "--timeout=240s",
            ],
            timeout=270,
        )
        tuning._checked(
            [
                "kubectl", "rollout", "status", "-n", namespace,
                "deployment/chunk-redis", "--timeout=240s",
            ],
            timeout=270,
        )
        chroma_forward = tuning._start_port_forward(
            output_dir,
            chroma_port,
            service=tuning.CHROMA_SERVICE,
            probe_path="/api/v2/heartbeat",
        )
        ollama_tags: dict[str, Any] | None = None
        for profile in profiles:
            profile_dir = output_dir / profile.name
            profile_dir.mkdir(parents=True, exist_ok=False)
            collection = tuning.collection_name(output_name, profile)
            config_name, config_map = tuning._profile_config_map(profile, output_name)
            job = tuning._profile_job(profile, output_name, config_name)
            configuration = {
                "role": profile.name,
                "profile": asdict(profile),
                "changed": {
                    "variable": "chunk_size",
                    "value": profile.chunk_size,
                },
                "unchanged": {
                    "chunk_overlap": profile.chunk_overlap,
                    **common,
                },
                "collection": collection,
                "cache_scope": tuning.cache_scope(common["CACHE_PREFIX"], collection),
                "namespace": namespace,
                "corpus_sha256": initial_corpus["sha256"],
                "golden_sha256": golden_hash,
            }
            write_json(profile_dir / "configuration.json", configuration)
            tuning._write_yaml(profile_dir / "profile-configmap.yaml", config_map)
            tuning._write_yaml(profile_dir / "indexer-job.yaml", job)
            existing = tuning._query_collection(collection)
            if existing["exists"]:
                raise PartC1Error(
                    f"collection {collection!r} already exists; refusing to overwrite"
                )
            if corpus_snapshot(CORPUS_ROOT)["sha256"] != initial_corpus["sha256"]:
                raise PartC1Error("corpus changed after the initial fingerprint")
            tuning._checked(
                ["kubectl", "apply", "-f", str(profile_dir / "profile-configmap.yaml")]
            )
            tuning._checked(["kubectl", "apply", "-f", str(profile_dir / "indexer-job.yaml")])
            tuning._run_indexer(profile_dir, str(job["metadata"]["name"]))
            if corpus_snapshot(CORPUS_ROOT)["sha256"] != initial_corpus["sha256"]:
                raise PartC1Error("corpus changed while the indexer was running")
            collection_info = tuning._query_collection(collection)
            count = collection_info.get("count")
            collection_metadata = collection_info.get("metadata")
            if (
                not collection_info.get("exists")
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count <= 0
                or not isinstance(collection_metadata, dict)
                or collection_metadata.get("embed_model") != common["EMBED_MODEL"]
            ):
                raise PartC1Error(f"indexed collection validation failed for {collection!r}")
            write_json(
                profile_dir / "collection.json",
                {
                    "name": collection,
                    "count": count,
                    "metadata": collection_metadata,
                    "corpus_sha256": initial_corpus["sha256"],
                },
            )

            tuning._checked(
                ["kubectl", "scale", "-n", namespace, "deployment/chunk-api", "--replicas=0"]
            )
            tuning._checked(
                [
                    "kubectl", "set", "env", "-n", namespace,
                    "deployment/chunk-api", f"CHROMA_COLLECTION={collection}",
                ]
            )
            tuning._checked(
                ["kubectl", "scale", "-n", namespace, "deployment/chunk-api", "--replicas=1"]
            )
            tuning._checked(
                [
                    "kubectl", "rollout", "status", "-n", namespace,
                    "deployment/chunk-api", "--timeout=240s",
                ],
                timeout=270,
            )
            api_image = api_image_reference(namespace)
            known_images = set(metadata["api_image_references"].values())
            if known_images and api_image not in known_images:
                raise PartC1Error("API image identity changed between configurations")
            metadata["api_image_references"][profile.name] = api_image
            configuration["api_image_reference"] = api_image
            write_json(profile_dir / "configuration.json", configuration)
            write_json(output_dir / "run.json", metadata)
            current_tags = tuning._ollama_tags()
            if ollama_tags is None:
                ollama_tags = current_tags
                metadata["ollama_model_tags"] = current_tags
            elif current_tags != ollama_tags:
                raise PartC1Error("Ollama model identity changed between configurations")
            process, stream = tuning._start_port_forward(profile_dir, local_port)
            try:
                suite_dir = part_b.run(
                    api=f"http://127.0.0.1:{local_port}",
                    api_image=api_image,
                    host=api_host,
                    golden=golden_path,
                    output_root=profile_dir / "part-b",
                    ollama_url=common["OLLAMA_URL"],
                    embedding_model=common["EMBED_MODEL"],
                )
            finally:
                tuning._stop_port_forward(process, stream)
            suite_summary = validate_suite(suite_dir / "summary.json", len(questions))
            measured[profile.name] = {
                "configuration": configuration,
                "collection": json.loads(
                    (profile_dir / "collection.json").read_text(encoding="utf-8")
                ),
                "suite_dir": str(suite_dir),
                "summary": suite_summary,
            }
            write_json(profile_dir / "result-reference.json", measured[profile.name])
            tuning._checked(
                ["kubectl", "scale", "-n", namespace, "deployment/chunk-api", "--replicas=0"]
            )
            tuning._wait_for_api_shutdown()

        if set(measured) != {"baseline", "candidate"}:
            raise PartC1Error("both baseline and candidate evaluations are required")
        comparison = compare_summaries(
            measured["baseline"]["summary"],
            measured["candidate"]["summary"],
        )
        raw = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "baseline": measured["baseline"],
            "candidate": measured["candidate"],
            "held_constant": common,
        }
        report = {
            "schema_version": 1,
            "experiment": "assignment-03-part-c1",
            "generated_at": raw["generated_at"],
            "change": {
                "variable": "chunk_size",
                "baseline": asdict(profiles[0]),
                "candidate": asdict(profiles[1]),
            },
            "held_constant": common,
            "collection_isolation": {
                profile.name: {
                    "collection": measured[profile.name]["configuration"]["collection"],
                    "cache_scope": measured[profile.name]["configuration"]["cache_scope"],
                    "chunk_count": measured[profile.name]["collection"]["count"],
                }
                for profile in profiles
            },
            "comparison": comparison,
            "part_b_result_paths": {
                profile.name: measured[profile.name]["suite_dir"]
                for profile in profiles
            },
            "live_deployment_changed": False,
        }
        write_json(output_dir / "individual-results.json", raw)
        write_json(output_dir / "comparison.json", report)
        (output_dir / "comparison.md").write_text(
            comparison_markdown(comparison, raw), encoding="utf-8"
        )
        metadata["status"] = "completed"
        metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
        metadata["comparison"] = "comparison.json"
        write_json(output_dir / "run.json", metadata)
    except Exception as exc:
        metadata["status"] = "failed"
        metadata["failed_at"] = datetime.now(timezone.utc).isoformat()
        metadata["error"] = f"{type(exc).__name__}: {exc}"
        write_json(output_dir / "run.json", metadata)
        raise
    finally:
        if chroma_forward is not None:
            tuning._stop_port_forward(*chroma_forward)
    return output_dir


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Run isolated Assignment 03 Part C1 baseline and candidate evaluations"
    )
    result.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    result.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    result.add_argument(
        "--output-dir",
        type=Path,
        help="new directory under eval/results (default: generated timestamped run)",
    )
    result.add_argument("--port", type=int, default=18082)
    result.add_argument("--chroma-port", type=int, default=18083)
    result.add_argument("--host", default="rahbar.local")
    result.add_argument(
        "--corpus-node-path",
        default="/mnt/rahbar-corpus",
        help="corpus directory as seen by the Kubernetes node",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    output_dir = args.output_dir
    if output_dir is None:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%SZ").lower()
        output_dir = DEFAULT_OUTPUT_ROOT / f"part-c1-{run_id}"
    elif not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    try:
        completed = run_experiment(
            output_dir=output_dir,
            config_path=args.config.resolve(),
            golden_path=args.golden.resolve(),
            local_port=args.port,
            chroma_port=args.chroma_port,
            api_host=args.host,
            corpus_node_path=args.corpus_node_path,
        )
    except (PartC1Error, tuning.ExperimentError, OSError, ValueError) as exc:
        print(f"Part C1 failed: {exc}", file=sys.stderr)
        return 1
    print(f"Part C1 completed. Results: {completed}")
    print(f"Comparison: {completed / 'comparison.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
