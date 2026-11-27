#!/usr/bin/env python3
"""Apply Assignment 03 Part C2 release thresholds to measured RAHBAR results."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

import yaml  # type: ignore[import-untyped]


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_CONFIG = HERE / "release-gate.yaml"
DEFAULT_OUTPUT_ROOT = HERE / "results" / "release-gates"


class ReleaseGateError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseGateError(f"cannot read JSON result {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReleaseGateError(f"result must be a JSON object: {path}")
    return payload


def read_yaml(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ReleaseGateError(f"cannot read threshold configuration {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReleaseGateError(f"threshold configuration must be a mapping: {path}")
    return payload


def finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReleaseGateError(f"{name} must be a number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ReleaseGateError(f"{name} must be finite")
    return number


def load_thresholds(path: Path) -> dict[str, Any]:
    payload = read_yaml(path)
    if payload.get("schema_version") != 1:
        raise ReleaseGateError("threshold configuration requires schema_version 1")
    thresholds = payload.get("thresholds")
    if not isinstance(thresholds, dict):
        raise ReleaseGateError("threshold configuration requires a thresholds mapping")

    allowed = {
        "max_recall_at_5_regression_percentage_points",
        "min_faithfulness_percentage",
        "max_p95_latency_seconds",
        "abstention_not_lower_than_baseline",
    }
    unknown = sorted(set(thresholds) - allowed)
    missing = sorted(allowed - set(thresholds))
    if unknown:
        raise ReleaseGateError(f"unknown threshold keys: {', '.join(unknown)}")
    if missing:
        raise ReleaseGateError(f"missing threshold keys: {', '.join(missing)}")

    recall = finite_number(
        thresholds["max_recall_at_5_regression_percentage_points"],
        "max_recall_at_5_regression_percentage_points",
    )
    faithfulness = finite_number(
        thresholds["min_faithfulness_percentage"],
        "min_faithfulness_percentage",
    )
    p95 = finite_number(
        thresholds["max_p95_latency_seconds"],
        "max_p95_latency_seconds",
    )
    abstention_relative = thresholds["abstention_not_lower_than_baseline"]
    if isinstance(abstention_relative, bool) is False:
        raise ReleaseGateError("abstention_not_lower_than_baseline must be true or false")
    if recall < 0 or recall > 100:
        raise ReleaseGateError("maximum Recall@5 regression must be from 0 to 100 percentage points")
    if faithfulness < 0 or faithfulness > 100:
        raise ReleaseGateError("minimum faithfulness must be from 0 to 100 percent")
    if p95 < 0:
        raise ReleaseGateError("maximum p95 latency must be non-negative")

    return {
        "max_recall_at_5_regression_percentage_points": recall,
        "min_faithfulness_percentage": faithfulness,
        "max_p95_latency_seconds": p95,
        "abstention_not_lower_than_baseline": abstention_relative,
    }


def mean_two_runs(payload: dict[str, Any], metric: str, level: str) -> float:
    runs = payload.get("runs")
    if not isinstance(runs, dict):
        raise ReleaseGateError("result is missing the runs mapping")
    values: list[float] = []
    for run_number in ("1", "2"):
        run = runs.get(run_number)
        if not isinstance(run, dict):
            raise ReleaseGateError(f"result is missing run {run_number}")
        level_data = run.get(level)
        if not isinstance(level_data, dict):
            raise ReleaseGateError(f"run {run_number} is missing {level} metrics")
        value = level_data.get(metric)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ReleaseGateError(
                f"metric unavailable: run {run_number}.{level}.{metric}"
            )
        values.append(finite_number(value, f"run {run_number}.{level}.{metric}"))
    return sum(values) / len(values)


def validate_part_b_result(payload: dict[str, Any], source_name: str) -> None:
    runs = payload.get("runs")
    if not isinstance(runs, dict) or set(runs) != {"1", "2"}:
        raise ReleaseGateError(f"{source_name}: result must contain exactly runs 1 and 2")

    required_metrics = {
        "module": (
            "mean_paraphrase_cosine",
            "mean_unrelated_cosine",
            "paraphrase_minus_unrelated",
            "embedding_time_mean_seconds",
            "embedding_time_p95_seconds",
            "largest_determinism_difference",
        ),
        "component": (
            "baseline_recall_at_3",
            "baseline_recall_at_5",
            "baseline_mrr",
            "baseline_ndcg_at_5",
            "typo_recall_at_3",
            "typo_recall_at_5",
            "typo_mrr",
            "typo_ndcg_at_5",
        ),
        "end_to_end": (
            "faithfulness_percentage",
            "abstention_correctness_percentage",
            "citation_validity_percentage",
            "latency_p50_seconds",
            "latency_p95_seconds",
        ),
    }
    successful_counts: list[int] = []
    metric_bounds = {
        "module": {
            "mean_paraphrase_cosine": (-1.0, 1.0),
            "mean_unrelated_cosine": (-1.0, 1.0),
            "paraphrase_minus_unrelated": (-2.0, 2.0),
            "embedding_time_mean_seconds": (0.0, None),
            "embedding_time_p95_seconds": (0.0, None),
            "largest_determinism_difference": (0.0, None),
        },
        "component": {
            metric: (0.0, 1.0)
            for metric in (
                "baseline_recall_at_3",
                "baseline_recall_at_5",
                "baseline_mrr",
                "baseline_ndcg_at_5",
                "typo_recall_at_3",
                "typo_recall_at_5",
                "typo_mrr",
                "typo_ndcg_at_5",
            )
        },
        "end_to_end": {
            metric: (0.0, 100.0)
            for metric in (
                "faithfulness_percentage",
                "abstention_correctness_percentage",
                "citation_validity_percentage",
            )
        } | {
            "latency_p50_seconds": (0.0, None),
            "latency_p95_seconds": (0.0, None),
        },
    }
    for run_number in ("1", "2"):
        run = runs[run_number]
        if not isinstance(run, dict):
            raise ReleaseGateError(f"{source_name}: run {run_number} must be a mapping")
        for level, metrics in required_metrics.items():
            values = run.get(level)
            if not isinstance(values, dict):
                raise ReleaseGateError(
                    f"{source_name}: run {run_number} is missing {level} metrics"
                )
            for metric in metrics:
                value = values.get(metric)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ReleaseGateError(
                        f"{source_name}: metric unavailable: "
                        f"run {run_number}.{level}.{metric}"
                    )
                finite_number(value, f"run {run_number}.{level}.{metric}")
                lower, upper = metric_bounds.get(level, {}).get(metric, (None, None))
                numeric = float(value)
                if lower is not None and numeric < lower:
                    raise ReleaseGateError(
                        f"{source_name}: run {run_number}.{level}.{metric} "
                        f"is below {lower}"
                    )
                if upper is not None and numeric > upper:
                    raise ReleaseGateError(
                        f"{source_name}: run {run_number}.{level}.{metric} "
                        f"is above {upper}"
                    )

        end_to_end = run["end_to_end"]
        successful = end_to_end.get("successful_requests")
        failures = end_to_end.get("request_failures")
        if (
            isinstance(successful, bool)
            or not isinstance(successful, int)
            or successful < 25
        ):
            raise ReleaseGateError(
                f"{source_name}: run {run_number} has fewer than 25 successful answers"
            )
        if isinstance(failures, bool) or not isinstance(failures, int) or failures != 0:
            raise ReleaseGateError(
                f"{source_name}: run {run_number} has incomplete end-to-end requests"
            )
        successful_counts.append(successful)
    if successful_counts[0] != successful_counts[1]:
        raise ReleaseGateError(
            f"{source_name}: the two runs evaluated different question counts"
        )


def extract_metrics(payload: dict[str, Any], source_name: str) -> dict[str, float]:
    validate_part_b_result(payload, source_name)
    successful_count = payload["runs"]["1"]["end_to_end"]["successful_requests"]
    try:
        recall = mean_two_runs(payload, "baseline_recall_at_5", "component")
    except ReleaseGateError as exc:
        raise ReleaseGateError(f"{source_name}: {exc}") from exc
    try:
        faithfulness = mean_two_runs(payload, "faithfulness_percentage", "end_to_end")
        abstention = mean_two_runs(
            payload,
            "abstention_correctness_percentage",
            "end_to_end",
        )
        p95 = mean_two_runs(payload, "latency_p95_seconds", "end_to_end")
    except ReleaseGateError as exc:
        raise ReleaseGateError(f"{source_name}: {exc}") from exc
    return {
        "recall_at_5": recall * 100.0,
        "faithfulness": faithfulness,
        "p95_latency_seconds": p95,
        "abstention_correctness": abstention,
        "successful_requests": float(successful_count),
    }


def evaluate_gate(
    baseline: dict[str, float],
    candidate: dict[str, float],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    if baseline.get("successful_requests") != candidate.get("successful_requests"):
        raise ReleaseGateError(
            "baseline and candidate evaluated different numbers of questions"
        )
    recall_regression = baseline["recall_at_5"] - candidate["recall_at_5"]
    checks = [
        {
            "metric": "recall_at_5_regression",
            "passed": recall_regression <= thresholds[
                "max_recall_at_5_regression_percentage_points"
            ],
            "baseline": baseline["recall_at_5"],
            "candidate": candidate["recall_at_5"],
            "value": recall_regression,
            "threshold": thresholds[
                "max_recall_at_5_regression_percentage_points"
            ],
            "reason": (
                f"Recall@5 regression is {recall_regression:.6f} percentage points "
                f"(limit "
                f"{thresholds['max_recall_at_5_regression_percentage_points']:.6f})."
            ),
        },
        {
            "metric": "faithfulness",
            "passed": candidate["faithfulness"]
            >= thresholds["min_faithfulness_percentage"],
            "baseline": baseline["faithfulness"],
            "candidate": candidate["faithfulness"],
            "value": candidate["faithfulness"],
            "threshold": thresholds["min_faithfulness_percentage"],
            "reason": (
                f"Faithfulness is {candidate['faithfulness']:.6f}% "
                f"(minimum {thresholds['min_faithfulness_percentage']:.6f}%)."
            ),
        },
        {
            "metric": "p95_latency",
            "passed": candidate["p95_latency_seconds"]
            <= thresholds["max_p95_latency_seconds"],
            "baseline": baseline["p95_latency_seconds"],
            "candidate": candidate["p95_latency_seconds"],
            "value": candidate["p95_latency_seconds"],
            "threshold": thresholds["max_p95_latency_seconds"],
            "reason": (
                f"p95 latency is {candidate['p95_latency_seconds']:.6f}s "
                f"(maximum {thresholds['max_p95_latency_seconds']:.6f}s)."
            ),
        },
        {
            "metric": "abstention_correctness",
            "passed": (
                candidate["abstention_correctness"]
                >= baseline["abstention_correctness"]
                if thresholds["abstention_not_lower_than_baseline"]
                else True
            ),
            "baseline": baseline["abstention_correctness"],
            "candidate": candidate["abstention_correctness"],
            "value": candidate["abstention_correctness"],
            "threshold": baseline["abstention_correctness"]
            if thresholds["abstention_not_lower_than_baseline"]
            else None,
            "reason": (
                f"Abstention correctness is "
                f"{candidate['abstention_correctness']:.6f}% versus baseline "
                f"{baseline['abstention_correctness']:.6f}%."
                if thresholds["abstention_not_lower_than_baseline"]
                else "Abstention-relative check disabled in configuration."
            ),
        },
    ]
    return {
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
        "failed_checks": [check["metric"] for check in checks if not check["passed"]],
    }


def write_report(
    output_dir: Path,
    report_name: str,
    baseline_path: Path,
    candidate_path: Path,
    config_path: Path,
    thresholds: dict[str, Any],
    baseline: dict[str, float],
    candidate: dict[str, float],
    gate: dict[str, Any],
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decision": "PASS" if gate["passed"] else "FAIL",
        "baseline_result": str(baseline_path.resolve()),
        "candidate_result": str(candidate_path.resolve()),
        "threshold_config": str(config_path.resolve()),
        "thresholds": thresholds,
        "baseline_metrics": baseline,
        "candidate_metrics": candidate,
        "gate": gate,
    }
    json_path = output_dir / f"{report_name}.json"
    md_path = output_dir / f"{report_name}.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# RAHBAR release gate",
        "",
        f"Decision: **{payload['decision']}**",
        "",
        "| Check | Baseline | Candidate | Threshold | Result | Reason |",
        "|---|---:|---:|---:|---|---|",
    ]
    for check in gate["checks"]:
        threshold = "disabled" if check["threshold"] is None else f"{check['threshold']:.6f}"
        lines.append(
            "| {metric} | {baseline:.6f} | {candidate:.6f} | {threshold} | {result} | {reason} |".format(
                metric=check["metric"],
                baseline=check["baseline"],
                candidate=check["candidate"],
                threshold=threshold,
                result="PASS" if check["passed"] else "FAIL",
                reason=check["reason"],
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Apply Assignment 03 C2 release gate")
    result.add_argument("--baseline", required=True, type=Path)
    result.add_argument("--candidate", required=True, type=Path)
    result.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    result.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_ROOT)
    result.add_argument("--name", default="gate", help="report base name")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        thresholds = load_thresholds(args.config)
        baseline_payload = read_json(args.baseline)
        candidate_payload = read_json(args.candidate)
        baseline = extract_metrics(baseline_payload, "baseline")
        candidate = extract_metrics(candidate_payload, "candidate")
        gate = evaluate_gate(baseline, candidate, thresholds)
        json_path, md_path = write_report(
            args.out,
            args.name,
            args.baseline,
            args.candidate,
            args.config,
            thresholds,
            baseline,
            candidate,
            gate,
        )
    except ReleaseGateError as exc:
        print(f"FAIL: invalid gate input: {exc}", file=sys.stderr)
        return 2

    print("PASS" if gate["passed"] else "FAIL")
    for check in gate["checks"]:
        print(
            f"{'PASS' if check['passed'] else 'FAIL'} "
            f"{check['metric']}: {check['reason']}"
        )
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {md_path}")
    return 0 if gate["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
