from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from eval import run_k8s_eval


def test_verify_dependency_reports_timeout_with_endpoint_context() -> None:
    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("network policy denied", request=_)

    client = httpx.Client(transport=httpx.MockTransport(timeout))

    with pytest.raises(
        run_k8s_eval.EvaluationPreflightError,
        match=r"api_health timed out at http://api:8000/healthz",
    ):
        run_k8s_eval.verify_dependency(
            client,
            name="api_health",
            url="http://api:8000/healthz",
        )


def test_run_preflight_checks_api_chroma_and_ollama(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requested: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, json={"status": "ok"})

    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **_: original_client(
            transport=httpx.MockTransport(respond),
            headers={"X-Test": "preflight"},
        ),
    )
    run_k8s_eval.run_preflight(
        api="http://api:8000",
        chroma_url="http://chroma:8000",
        ollama_url="http://ollama:11434",
        timeout=3.0,
    )

    assert requested == [
        "http://api:8000/healthz",
        "http://chroma:8000/api/v2/heartbeat",
        "http://ollama:11434/api/tags",
    ]


def test_main_reports_preflight_failure_without_starting_suite(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        run_k8s_eval,
        "run_preflight",
        lambda **_: (_ for _ in ()).throw(
            run_k8s_eval.EvaluationPreflightError(
                "api_health timed out at http://api:8000/healthz"
            )
        ),
    )
    monkeypatch.setattr(
        run_k8s_eval.part_b,
        "run",
        lambda **_: pytest.fail("suite started after failed preflight"),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_k8s_eval.py",
            "--api",
            "http://api:8000",
            "--api-image",
            "docker.io/library/rahbar-api@sha256:"
            "a" * 64,
            "--golden",
            str(tmp_path / "golden.jsonl"),
            "--out-root",
            str(tmp_path / "results"),
            "--baseline",
            str(tmp_path / "baseline.json"),
            "--thresholds",
            str(tmp_path / "gate.yaml"),
            "--ollama-url",
            "http://ollama:11434",
            "--embedding-model",
            "nomic-embed-text",
        ],
    )

    assert run_k8s_eval.main() == 2
    events = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip()
    ]
    assert events[-1]["event"] == "kubernetes_evaluation"
    assert events[-1]["message"] == "evaluation failed"
    assert "api_health timed out" in events[-1]["error"]


@pytest.mark.parametrize(("gate_passed", "expected_exit"), [(True, 0), (False, 1)])
def test_main_runs_successful_suite_and_applies_real_gate_decision(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    gate_passed: bool,
    expected_exit: int,
) -> None:
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    candidate_path = suite_dir / "summary.json"
    candidate_path.write_text("{}", encoding="utf-8")
    report_path = suite_dir / "gate.json"

    monkeypatch.setattr(run_k8s_eval, "run_preflight", lambda **_: None)
    monkeypatch.setattr(run_k8s_eval.part_b, "run", lambda **_: suite_dir)
    monkeypatch.setattr(run_k8s_eval.release_gate, "load_thresholds", lambda _: {})
    monkeypatch.setattr(
        run_k8s_eval.release_gate,
        "read_json",
        lambda _: {},
    )
    monkeypatch.setattr(
        run_k8s_eval.release_gate,
        "extract_metrics",
        lambda *_: {},
    )
    monkeypatch.setattr(
        run_k8s_eval.release_gate,
        "evaluate_gate",
        lambda *_: {
            "passed": gate_passed,
            "failed_checks": [] if gate_passed else ["faithfulness"],
        },
    )
    monkeypatch.setattr(
        run_k8s_eval.release_gate,
        "write_report",
        lambda *_args, **_kwargs: (report_path, suite_dir / "gate.md"),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_k8s_eval.py",
            "--api",
            "http://api:8000",
            "--api-image",
            "docker.io/library/rahbar-api@sha256:"
            "a" * 64,
            "--golden",
            str(tmp_path / "golden.jsonl"),
            "--out-root",
            str(tmp_path / "results"),
            "--baseline",
            str(tmp_path / "baseline.json"),
            "--thresholds",
            str(tmp_path / "gate.yaml"),
            "--ollama-url",
            "http://ollama:11434",
            "--embedding-model",
            "nomic-embed-text",
        ],
    )

    assert run_k8s_eval.main() == expected_exit
    events = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip()
    ]
    assert events[-1]["message"] == "release gate complete"
    assert events[-1]["status"] == ("PASS" if gate_passed else "FAIL")
