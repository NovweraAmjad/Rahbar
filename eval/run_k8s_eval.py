#!/usr/bin/env python3
"""Run the complete Part B suite in Kubernetes and apply the release gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

import httpx
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import run_part_b_suite as part_b  # noqa: E402
from eval import run_release_gate as release_gate  # noqa: E402


DEFAULT_PREFLIGHT_TIMEOUT_SECONDS = 5.0


class EvaluationPreflightError(RuntimeError):
    pass


def emit(message: str, **fields: object) -> None:
    print(
        json.dumps(
            {"event": "kubernetes_evaluation", "message": message, **fields},
            sort_keys=True,
        ),
        flush=True,
    )


def endpoint_label(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.hostname:
        return url
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}{parsed.path}"


def verify_dependency(
    client: httpx.Client,
    *,
    name: str,
    url: str,
    expected_statuses: tuple[int, ...] = (200,),
) -> None:
    emit("checking dependency", dependency=name, url=endpoint_label(url))
    try:
        response = client.get(url)
    except httpx.TimeoutException as exc:
        raise EvaluationPreflightError(
            f"{name} timed out at {endpoint_label(url)}; "
            "check NetworkPolicy ingress/egress, Service endpoints, and endpoint health"
        ) from exc
    except httpx.RequestError as exc:
        raise EvaluationPreflightError(
            f"{name} connection failed at {endpoint_label(url)}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if response.status_code not in expected_statuses:
        raise EvaluationPreflightError(
            f"{name} returned HTTP {response.status_code} at "
            f"{endpoint_label(url)}: {response.text[:200]}"
        )
    emit(
        "dependency available",
        dependency=name,
        url=endpoint_label(url),
        http_status=response.status_code,
    )


def run_preflight(
    *,
    api: str,
    chroma_url: str,
    ollama_url: str,
    timeout: float,
) -> None:
    if timeout <= 0:
        raise EvaluationPreflightError("preflight timeout must be positive")
    timeout_config = httpx.Timeout(timeout)
    with httpx.Client(timeout=timeout_config, follow_redirects=False) as client:
        verify_dependency(client, name="api_health", url=f"{api.rstrip('/')}/healthz")
        verify_dependency(
            client,
            name="chroma",
            url=f"{chroma_url.rstrip('/')}/api/v2/heartbeat",
        )
        verify_dependency(
            client,
            name="ollama",
            url=f"{ollama_url.rstrip('/')}/api/tags",
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Part B twice and fail the Job when the release gate fails"
    )
    parser.add_argument("--api", required=True)
    parser.add_argument("--api-image", required=True)
    parser.add_argument("--host", default="rahbar.local")
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--ollama-url", required=True)
    parser.add_argument("--embedding-model", required=True)
    parser.add_argument("--chroma-url", default="http://chroma:8000")
    parser.add_argument(
        "--preflight-timeout",
        type=float,
        default=DEFAULT_PREFLIGHT_TIMEOUT_SECONDS,
    )
    parser.add_argument("--name", default="kubernetes-evaluation-gate")
    args = parser.parse_args()

    try:
        emit(
            "starting preflight",
            api=endpoint_label(args.api),
            chroma=endpoint_label(args.chroma_url),
            ollama=endpoint_label(args.ollama_url),
            timeout_seconds=args.preflight_timeout,
        )
        run_preflight(
            api=args.api,
            chroma_url=args.chroma_url,
            ollama_url=args.ollama_url,
            timeout=args.preflight_timeout,
        )
        emit("starting Part B suite", run_count=2)
        suite_dir = part_b.run(
            api=args.api,
            api_image=args.api_image,
            host=args.host,
            golden=args.golden,
            output_root=args.out_root,
            ollama_url=args.ollama_url,
            embedding_model=args.embedding_model,
        )
        emit("Part B suite complete", suite_dir=str(suite_dir))
        candidate_path = suite_dir / "summary.json"
        thresholds = release_gate.load_thresholds(args.thresholds)
        baseline_payload = release_gate.read_json(args.baseline)
        candidate_payload = release_gate.read_json(candidate_path)
        baseline = release_gate.extract_metrics(baseline_payload, "baseline")
        candidate = release_gate.extract_metrics(candidate_payload, "candidate")
        gate = release_gate.evaluate_gate(baseline, candidate, thresholds)
        json_path, _markdown_path = release_gate.write_report(
            suite_dir,
            args.name,
            args.baseline,
            candidate_path,
            args.thresholds,
            thresholds,
            baseline,
            candidate,
            gate,
        )
    except (
        EvaluationPreflightError,
        release_gate.ReleaseGateError,
        RuntimeError,
        OSError,
        ValueError,
    ) as exc:
        emit("evaluation failed", error=f"{type(exc).__name__}: {exc}")
        return 2
    emit(
        "release gate complete",
        status="PASS" if gate["passed"] else "FAIL",
        suite_dir=str(suite_dir),
        gate_report=str(json_path),
        failed_checks=gate["failed_checks"],
    )
    return 0 if gate["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
