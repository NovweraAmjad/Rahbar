from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

import httpx
import pytest
import yaml

from eval import run_chunk_tuning as tuning


def _successful_evaluation(
    question_ids: list[str],
    *,
    gate_passed: bool = True,
) -> dict[str, Any]:
    return {
        "metrics": {
            "total_questions": len(question_ids),
            "successful_requests": len(question_ids),
            "request_failures": 0,
            "recall_at_3": 0.5,
            "recall_at_5": 0.75,
            "citation_provenance_validity_percentage": 95.0,
            "gold_evidence_relevance_percentage": 70.0,
            "must_abstain_correctness_percentage": 100.0,
        },
        "gate": {
            "passed": gate_passed,
            "failures": [] if gate_passed else ["Recall@5 gate failed"],
        },
        "questions": [
            {"id": question_id, "passed": True, "pass_reasons": []}
            for question_id in question_ids
        ],
    }


def test_loads_exact_approved_profiles_and_locked_settings() -> None:
    profiles = tuning.load_profiles(tuning.DEFAULT_PROFILES)
    assert [(item.name, item.chunk_size, item.chunk_overlap) for item in profiles] == [
        ("small", 200, 25),
        ("current", 400, 50),
        ("large", 800, 100),
    ]
    assert tuning.load_common_settings(tuning.EXPERIMENT_DIR / "configmap.yaml") == (
        tuning.LOCKED_SETTINGS
    )


@pytest.mark.parametrize(
    "profiles",
    [
        [{"name": "small", "chunk_size": 0, "chunk_overlap": 0}],
        [{"name": "small", "chunk_size": 200, "chunk_overlap": 200}],
        [
            {"name": "small", "chunk_size": 200, "chunk_overlap": 25},
            {"name": "small", "chunk_size": 400, "chunk_overlap": 50},
            {"name": "large", "chunk_size": 800, "chunk_overlap": 100},
        ],
        [
            {"name": "small", "chunk_size": 200, "chunk_overlap": 25},
            {"name": "current", "chunk_size": 400, "chunk_overlap": 50},
            {"name": "large", "chunk_size": 800, "chunk_overlap": 99},
        ],
    ],
)
def test_rejects_invalid_or_unapproved_profiles(
    tmp_path: Path,
    profiles: list[dict[str, Any]],
) -> None:
    path = tmp_path / "profiles.yaml"
    path.write_text(yaml.safe_dump({"profiles": profiles}), encoding="utf-8")
    with pytest.raises(tuning.ExperimentError):
        tuning.load_profiles(path)


def test_rejects_common_configuration_drift(tmp_path: Path) -> None:
    document = yaml.safe_load(
        (tuning.EXPERIMENT_DIR / "configmap.yaml").read_text(encoding="utf-8")
    )
    document["data"]["TOP_K"] = "10"
    path = tmp_path / "configmap.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(tuning.ExperimentError, match="locked retrieval configuration"):
        tuning.load_common_settings(path)


@pytest.mark.parametrize("existing_kind", ["directory", "file"])
def test_refuses_to_overwrite_existing_output_path(
    tmp_path: Path,
    existing_kind: str,
) -> None:
    output = tmp_path / "run"
    if existing_kind == "directory":
        output.mkdir()
    else:
        output.write_text("keep", encoding="utf-8")

    with pytest.raises(tuning.ExperimentError, match="refusing to overwrite"):
        tuning.validate_output_path(output)


def test_collection_and_semantic_cache_scope_are_isolated_per_profile_and_run() -> None:
    profiles = tuning.load_profiles(tuning.DEFAULT_PROFILES)
    collections = [tuning.collection_name("run-one", profile) for profile in profiles]
    scopes = [tuning.cache_scope("rahbar:semantic-cache", name) for name in collections]

    assert len(set(collections)) == 3
    assert len(set(scopes)) == 3
    assert all(len(name) <= 63 for name in collections)
    assert collections != [
        tuning.collection_name("run-two", profile) for profile in profiles
    ]


def test_collection_inspection_uses_chroma_http_api_without_pod_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection = "rahbar-ct-run-test-small"
    collection_id = "00000000-0000-0000-0000-000000000001"
    requests: list[tuple[str, float]] = []
    list_url = (
        f"http://127.0.0.1:{tuning.CHROMA_LOCAL_PORT}/api/v2/tenants/"
        "default_tenant/databases/default_database/collections"
    )
    count_url = f"{list_url}/{collection_id}/count"
    responses = iter(
        [
            httpx.Response(
                200,
                json=[
                    {
                        "id": "00000000-0000-0000-0000-000000000002",
                        "name": "another-collection",
                        "metadata": None,
                    },
                    {
                        "id": collection_id,
                        "name": collection,
                        "metadata": {"embed_model": "nomic-embed-text"},
                    },
                ],
                request=httpx.Request("GET", list_url),
            ),
            httpx.Response(
                200,
                json=456,
                request=httpx.Request("GET", count_url),
            ),
        ]
    )

    def get(url: str, *, timeout: float) -> httpx.Response:
        requests.append((url, timeout))
        return next(responses)

    monkeypatch.setattr(tuning.httpx, "get", get)
    monkeypatch.setattr(
        tuning,
        "_checked",
        lambda *_args, **_kwargs: pytest.fail("collection inspection must not exec in Chroma"),
    )
    result = tuning._query_collection(collection)

    assert requests == [
        (list_url, tuning.CHROMA_QUERY_TIMEOUT),
        (count_url, tuning.CHROMA_QUERY_TIMEOUT),
    ]
    assert result == {
        "exists": True,
        "count": 456,
        "metadata": {"embed_model": "nomic-embed-text"},
    }


def test_collection_inspection_reports_collection_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []

    def get(url: str, *, timeout: float) -> httpx.Response:
        requested_urls.append(url)
        return httpx.Response(200, json=[], request=httpx.Request("GET", url))

    monkeypatch.setattr(
        tuning.httpx,
        "get",
        get,
    )

    assert tuning._query_collection("missing-collection") == {"exists": False}
    assert requested_urls == [
        "http://127.0.0.1:18081/api/v2/tenants/default_tenant/"
        "databases/default_database/collections"
    ]


@pytest.mark.parametrize("payload", [{"collections": []}, [None]])
def test_collection_inspection_rejects_malformed_list_response(
    monkeypatch: pytest.MonkeyPatch, payload: Any
) -> None:
    monkeypatch.setattr(
        tuning.httpx,
        "get",
        lambda url, *, timeout: httpx.Response(
            200, json=payload, request=httpx.Request("GET", url)
        ),
    )

    with pytest.raises(tuning.ExperimentError, match="invalid collection list"):
        tuning._query_collection("expected-collection")


@pytest.mark.parametrize("collection_id", [None, "", "   "])
def test_collection_inspection_rejects_missing_or_invalid_collection_id(
    monkeypatch: pytest.MonkeyPatch, collection_id: Any
) -> None:
    payload = [{"id": collection_id, "name": "expected-collection", "metadata": {}}]
    monkeypatch.setattr(
        tuning.httpx,
        "get",
        lambda url, *, timeout: httpx.Response(
            200, json=payload, request=httpx.Request("GET", url)
        ),
    )

    with pytest.raises(tuning.ExperimentError, match="invalid collection metadata"):
        tuning._query_collection("expected-collection")


def test_collection_inspection_reports_count_request_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection_list = [
        {
            "id": "collection-id",
            "name": "expected-collection",
            "metadata": {"embed_model": "nomic-embed-text"},
        }
    ]
    calls: list[str] = []

    def get(url: str, *, timeout: float) -> httpx.Response:
        calls.append(url)
        if len(calls) == 1:
            return httpx.Response(200, json=collection_list, request=httpx.Request("GET", url))
        return httpx.Response(503, request=httpx.Request("GET", url))

    monkeypatch.setattr(tuning.httpx, "get", get)

    with pytest.raises(tuning.ExperimentError, match="count returned HTTP 503"):
        tuning._query_collection("expected-collection")

    assert calls[-1].endswith("/collections/collection-id/count")


def test_chroma_port_forward_probes_service_heartbeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    probed_urls: list[str] = []

    class Process:
        terminated = False

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, *, timeout: int) -> None:
            del timeout

    process = Process()

    def popen(command: list[str], **_kwargs: Any) -> Process:
        commands.append(command)
        return process

    def get(url: str, *, timeout: float) -> httpx.Response:
        assert timeout == 1.0
        probed_urls.append(url)
        return httpx.Response(
            200,
            json={"nanosecond heartbeat": 1},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(tuning.subprocess, "Popen", popen)
    monkeypatch.setattr(tuning.httpx, "get", get)

    forward = tuning._start_port_forward(
        tmp_path,
        tuning.CHROMA_LOCAL_PORT,
        service=tuning.CHROMA_SERVICE,
        probe_path="/api/v2/heartbeat",
    )
    tuning._stop_port_forward(*forward)

    assert commands[0][commands[0].index("service/chunk-chroma") + 1] == "18081:8000"
    assert probed_urls == ["http://127.0.0.1:18081/api/v2/heartbeat"]
    assert process.terminated


def test_evaluate_profile_passes_experiment_request_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_dir = tmp_path / "small"
    profile_dir.mkdir()
    golden_path = tmp_path / "golden.yaml"
    baseline_path = tmp_path / "baseline.json"
    calls: dict[str, Any] = {}

    monkeypatch.setattr(tuning, "_start_port_forward", lambda *_args: (None, None))
    monkeypatch.setattr(tuning, "_stop_port_forward", lambda *_args: None)
    monkeypatch.setattr(tuning, "_load_golden_ids", lambda _path: ["q01"])

    def run(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        calls["command"] = command
        calls["timeout"] = timeout
        output_path = Path(command[command.index("--out") + 1])
        output_path.write_text(
            json.dumps(_successful_evaluation(["q01"])),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

    monkeypatch.setattr(tuning, "_run", run)

    _evaluation, _output = tuning._evaluate_profile(
        profile_dir,
        "rahbar.local",
        18080,
        golden_path,
        baseline_path,
    )

    command = calls["command"]
    timeout_index = command.index("--timeout")
    assert command[timeout_index + 1] == str(tuning.EVALUATOR_REQUEST_TIMEOUT_SECONDS)
    assert tuning.EVALUATOR_REQUEST_TIMEOUT_SECONDS == 150
    assert calls["timeout"] == tuning.EVALUATION_TIMEOUT
    assert tuning.EVALUATION_TIMEOUT == 45 * 60


def test_generated_profile_config_and_job_use_run_specific_collection() -> None:
    profile = tuning.load_profiles(tuning.DEFAULT_PROFILES)[0]
    config_name, config_map = tuning._profile_config_map(profile, "run-test")
    job = tuning._profile_job(profile, "run-test", config_name)

    assert config_map["data"] == {
        "CHROMA_COLLECTION": "rahbar-ct-run-test-small",
        "CHUNK_SIZE": "200",
        "CHUNK_OVERLAP": "25",
    }
    refs = job["spec"]["template"]["spec"]["containers"][0]["envFrom"]
    assert refs[0]["configMapRef"]["name"] == "chunk-tuning-common"
    assert refs[1]["configMapRef"]["name"] == config_name
    assert job["metadata"]["name"] == "indexer-run-test-small"


def test_corpus_snapshot_verifies_indexed_file_checksums_and_fingerprint(
    tmp_path: Path,
) -> None:
    source = b"corpus source bytes"
    (tmp_path / "source.pdf").write_bytes(source)
    index = [
        {
            "file": "source.pdf",
            "sha256": hashlib.sha256(source).hexdigest(),
            "doc_version": "test",
            "superseded": False,
        }
    ]
    (tmp_path / "_index.json").write_text(json.dumps(index), encoding="utf-8")
    (tmp_path.parent / "MANIFEST.md").write_text(
        "\n".join(
            [
                "| Document | Version | Superseded | File | Bytes | SHA-256 | URL |",
                "| --- | --- | --- | --- | ---: | --- | --- |",
                f"| Example | test | no | `source.pdf` | {len(source)} | "
                f"`{hashlib.sha256(source).hexdigest()}` | <https://example.test> |",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = tuning.corpus_snapshot(tmp_path)
    assert result["files"]["source.pdf"] == hashlib.sha256(source).hexdigest()
    assert result["files"]["_index.json"] == tuning._file_sha256(tmp_path / "_index.json")
    assert len(result["sha256"]) == 64

    (tmp_path / "source.pdf").write_bytes(b"changed")
    with pytest.raises(tuning.ExperimentError, match="SHA-256 mismatch"):
        tuning.corpus_snapshot(tmp_path)


def test_rejects_incomplete_or_failed_evaluation_responses() -> None:
    ids = ["q01", "q02"]
    complete = _successful_evaluation(ids)
    assert tuning._validate_complete_evaluation(complete, ids) is complete

    request_failed = _successful_evaluation(ids)
    request_failed["metrics"]["successful_requests"] = 1
    request_failed["metrics"]["request_failures"] = 1
    with pytest.raises(tuning.ExperimentError, match="requests were incomplete"):
        tuning._validate_complete_evaluation(request_failed, ids)

    missing_question = _successful_evaluation(ids[:1])
    with pytest.raises(tuning.ExperimentError, match="exact golden question set"):
        tuning._validate_complete_evaluation(missing_question, ids)


def _mock_experiment_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    fail_evaluation: bool = False,
    metric_gate_failure_profile: str | None = None,
    existing_collection: bool = False,
) -> tuple[list[tuple[str, str]], Path]:
    events: list[tuple[str, str]] = []
    baseline = tuning.DEFAULT_BASELINE
    question_ids = [f"q{index:02d}" for index in range(1, 16)]
    corpus = {"sha256": "a" * 64, "files": {"doc.pdf": "b" * 64}}
    collection_checks: dict[str, int] = {}

    def snapshot(path: Path) -> dict[str, Any]:
        assert path == tuning.CORPUS_DIR
        return corpus

    def verify_corpus() -> str:
        events.append(("verify", "corpus"))
        return "verified\n"

    monkeypatch.setattr(tuning, "corpus_snapshot", snapshot)
    monkeypatch.setattr(tuning, "_verify_manifest_corpus", verify_corpus)
    monkeypatch.setattr(tuning, "_git_commit", lambda: "abc123")
    monkeypatch.setattr(
        tuning,
        "_read_image_declarations",
        lambda: {"api": "api:dev", "indexer": "indexer:dev", "chroma": "chroma:1", "redis": "redis:7"},
    )
    monkeypatch.setattr(tuning, "_check_namespace_is_new", lambda: None)
    monkeypatch.setattr(tuning, "_load_golden_ids", lambda _path: question_ids)

    def checked(command: list[str] | tuple[str, ...], *, timeout: int = 180):
        del timeout
        events.append(("kubectl", " ".join(command)))
        return subprocess.CompletedProcess(list(command), 0, stdout="", stderr="")

    def collection_state(name: str) -> dict[str, Any]:
        collection_checks[name] = collection_checks.get(name, 0) + 1
        if collection_checks[name] == 1:
            return {"exists": existing_collection, "count": 10, "metadata": {}}
        return {
            "exists": True,
            "count": 456,
            "metadata": {"embed_model": "nomic-embed-text", "dimension": 768},
        }

    def indexer(profile_dir: Path, job: str) -> None:
        events.append(("index", profile_dir.name))
        (profile_dir / "indexing.log").write_text(f"indexed {job}\n", encoding="utf-8")

    def evaluate(
        profile_dir: Path,
        host: str,
        port: int,
        golden: Path,
        baseline_path: Path,
    ) -> tuple[dict[str, Any], str]:
        assert host == "rahbar.local"
        assert port == 18080
        assert golden == tuning.DEFAULT_GOLDEN
        assert baseline_path == baseline.resolve()
        events.append(("evaluate", profile_dir.name))
        if fail_evaluation and profile_dir.name == "small":
            raise tuning.ExperimentError("simulated incomplete API evaluation")
        gate_passed = profile_dir.name != metric_gate_failure_profile
        return (
            _successful_evaluation(question_ids, gate_passed=gate_passed),
            "evaluation complete\n",
        )

    monkeypatch.setattr(tuning, "_checked", checked)
    monkeypatch.setattr(tuning, "_start_port_forward", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(tuning, "_stop_port_forward", lambda *_args: None)
    monkeypatch.setattr(tuning, "_wait_for_api_shutdown", lambda: None)
    monkeypatch.setattr(tuning, "_query_collection", collection_state)
    monkeypatch.setattr(tuning, "_run_indexer", indexer)
    def image_ids(selector: str) -> list[dict[str, Any]]:
        assert selector
        return [{"pod": "pod-1"}]

    monkeypatch.setattr(tuning, "_pod_image_ids", image_ids)
    monkeypatch.setattr(
        tuning,
        "_ollama_tags",
        lambda: {
            "EMBED_MODEL": {"configured": "nomic-embed-text", "name": "nomic-embed-text:latest"},
            "LLM_MODEL": {"configured": "llama3.2:3b", "name": "llama3.2:3b"},
        },
    )
    monkeypatch.setattr(tuning, "_evaluate_profile", evaluate)
    return events, baseline


def test_runner_completes_profiles_sequentially_and_writes_comparison(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, baseline = _mock_experiment_dependencies(monkeypatch, tmp_path)
    baseline_hash = tuning._file_sha256(baseline)
    output = tmp_path / "run-test"
    result = tuning.run_experiment(output_dir=output, baseline_path=baseline)

    assert result == output
    assert [
        item for item in events if item[0] in {"index", "evaluate"}
    ] == [
        ("index", "small"),
        ("evaluate", "small"),
        ("index", "current"),
        ("evaluate", "current"),
        ("index", "large"),
        ("evaluate", "large"),
    ]
    comparison = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    assert [item["profile"] for item in comparison["profiles"]] == [
        "small",
        "current",
        "large",
    ]
    assert all(item["chunk_count"] == 456 for item in comparison["profiles"])
    assert json.loads((output / "run.json").read_text(encoding="utf-8"))["status"] == (
        "completed"
    )
    assert tuning._file_sha256(baseline) == baseline_hash


def test_runner_stops_on_incomplete_profile_and_preserves_failure_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, baseline = _mock_experiment_dependencies(
        monkeypatch,
        tmp_path,
        fail_evaluation=True,
    )
    output = tmp_path / "run-failed"

    with pytest.raises(tuning.ExperimentError, match="simulated incomplete"):
        tuning.run_experiment(output_dir=output, baseline_path=baseline)

    assert [item for item in events if item[0] in {"index", "evaluate"}] == [
        ("index", "small"),
        ("evaluate", "small"),
    ]
    failure = json.loads((output / "small" / "failures.json").read_text(encoding="utf-8"))
    assert "simulated incomplete API evaluation" in failure["runner_error"]
    assert not (output / "comparison.json").exists()
    assert json.loads((output / "run.json").read_text(encoding="utf-8"))["status"] == "failed"


def test_runner_refuses_to_reuse_an_existing_chroma_collection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, baseline = _mock_experiment_dependencies(
        monkeypatch,
        tmp_path,
        existing_collection=True,
    )
    output = tmp_path / "run-collection"

    with pytest.raises(tuning.ExperimentError, match="already exists"):
        tuning.run_experiment(output_dir=output, baseline_path=baseline)

    assert not [item for item in events if item[0] in {"index", "evaluate"}]
    assert not (output / "comparison.json").exists()
    failure = json.loads((output / "small" / "failures.json").read_text(encoding="utf-8"))
    assert "refusing to reuse its data" in failure["runner_error"]


def test_runner_retains_complete_metric_gate_failures_in_comparison(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _events, baseline = _mock_experiment_dependencies(
        monkeypatch,
        tmp_path,
        metric_gate_failure_profile="current",
    )
    output = tmp_path / "run-gated"
    tuning.run_experiment(output_dir=output, baseline_path=baseline)

    comparison = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    assert comparison["profiles"][1]["evaluation_gate_passed"] is False
    assert comparison["profiles"][1]["evaluation_gate_failures"] == ["Recall@5 gate failed"]
    run = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert run["status"] == "completed_with_evaluation_failures"
    assert run["evaluation_gate_failed_profiles"] == ["current"]


def test_complete_evaluation_retains_question_failures_as_measurements() -> None:
    evaluation = _successful_evaluation(["q01", "q02"])
    evaluation["questions"][1] = {
        "id": "q02",
        "passed": False,
        "pass_reasons": ["Recall@5 missed gold evidence"],
    }

    checked = tuning._validate_complete_evaluation(evaluation, ["q01", "q02"])
    assert [item["id"] for item in tuning._question_failures(checked)] == ["q02"]
    row = tuning._comparison_row(
        tuning.ChunkProfile("small", 200, 25),
        123,
        checked,
        tuning._question_failures(checked),
    )
    assert row["chunk_count"] == 123
    assert row["failed_question_ids"] == ["q02"]
