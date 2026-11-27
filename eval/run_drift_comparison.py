#!/usr/bin/env python3
"""Compare real Part B results before and after the verified Batch B ingest."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.run_part_c1 import compare_summaries, format_value  # noqa: E402


HERE = Path(__file__).resolve().parent
DEFAULT_OUTPUT_ROOT = HERE / "results"
DRIFT_METRICS = {
    "component": (
        "baseline_recall_at_3",
        "baseline_recall_at_5",
        "baseline_mrr",
        "baseline_ndcg_at_5",
    ),
    "end_to_end": (
        "faithfulness_percentage",
        "abstention_correctness_percentage",
        "citation_validity_percentage",
        "latency_p50_seconds",
        "latency_p95_seconds",
    ),
}


class DriftComparisonError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DriftComparisonError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DriftComparisonError(f"JSON root must be an object: {path}")
    return payload


def validate_part_b_summary(payload: dict[str, Any], path: Path) -> None:
    runs = payload.get("runs")
    if not isinstance(runs, dict) or set(runs) != {"1", "2"}:
        raise DriftComparisonError(f"not a complete two-run Part B summary: {path}")
    for number in ("1", "2"):
        for level, metrics in DRIFT_METRICS.items():
            values = runs[number].get(level)
            if not isinstance(values, dict):
                raise DriftComparisonError(
                    f"{path}: run {number} is missing {level} metrics"
                )
            for metric in metrics:
                if not isinstance(values.get(metric), (int, float)) or isinstance(
                    values.get(metric), bool
                ):
                    raise DriftComparisonError(
                        f"{path}: run {number} has unavailable {level}.{metric}"
                    )


def ingestion_facts(payload: dict[str, Any]) -> dict[str, Any]:
    scenarios = payload.get("scenarios")
    if isinstance(scenarios, dict) and isinstance(scenarios.get("batch_b"), dict):
        batch_b = scenarios["batch_b"]
        return {
            "status": payload.get("status"),
            "collection": batch_b.get("collection"),
            "first_run": batch_b.get("first_run"),
            "unchanged_rerun": batch_b.get("unchanged_rerun"),
            "verification_summary": batch_b.get("verification_summary"),
            "quarantined_sources": batch_b.get("quarantined_sources"),
            "chunks_by_source": batch_b.get("chunks_by_source"),
            "live_user_collection_modified": payload.get(
                "live_user_collection_modified"
            ),
        }
    if "verification_summary" in payload or "batch_b_first_incremental" in payload:
        return payload
    raise DriftComparisonError(
        "ingestion evidence is neither a real Part A integration result nor "
        "the Batch B A2 evidence shape"
    )


def report_markdown(result: dict[str, Any]) -> str:
    comparison = result["comparison"]
    lines = [
        "# Assignment 03 Part E data drift",
        "",
        f"- Generated: {result['metadata']['generated_at']}",
        f"- Baseline: `{result['metadata']['baseline_summary']}`",
        f"- Post-Batch-B: `{result['metadata']['post_batch_b_summary']}`",
        f"- Ingestion evidence: `{result['metadata']['ingestion_evidence']}`",
        "",
        "## Batch B ingestion evidence",
        "",
        "```json",
        json.dumps(result["ingestion"], indent=2, ensure_ascii=False),
        "```",
        "",
        "## Metric changes",
        "",
        "| Level | Metric | Baseline mean | Post-Batch-B mean | Change |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for level, metric_names in DRIFT_METRICS.items():
        for metric in metric_names:
            row = comparison[level][metric]
            lines.append(
                f"| {level} | {metric} | {format_value(row['baseline_mean'])} | "
                f"{format_value(row['candidate_mean'])} | "
                f"{format_value(row['absolute_change_candidate_minus_baseline'])} |"
            )
    lines.extend(
        [
            "",
            "## Evidence-based interpretation",
            "",
            result["interpretation"],
            "",
            "No change is attributed to model or pipeline improvements. The "
            "delivered values are the arithmetic means of two real Part B runs; "
            "individual run evidence remains in the referenced summary directories.",
        ]
    )
    return "\n".join(lines) + "\n"


def interpret(comparison: dict[str, Any], ingestion: dict[str, Any]) -> str:
    verification = ingestion.get("verification_summary") or {}
    indexed = verification.get("index")
    quarantined = verification.get("quarantine")
    accepted_sources = sorted((ingestion.get("chunks_by_source") or {}).keys())
    return (
        f"Verification accepted {indexed} Batch B documents and quarantined "
        f"{quarantined}; accepted sources were "
        f"{', '.join(accepted_sources) if accepted_sources else 'not recorded'}. "
        f"Consequently, retrieval and answer changes can be traced to the added "
        f"active chunks, while quarantined duplicate, corrupt, truncated, "
        f"superseded, missing-metadata, and injection documents did not enter "
        f"the evaluated collection. Recall@5 changed by "
        f"{comparison['component']['baseline_recall_at_5']['absolute_change_candidate_minus_baseline']:+.6f}; "
        f"faithfulness changed by "
        f"{comparison['end_to_end']['faithfulness_percentage']['absolute_change_candidate_minus_baseline']:+.6f} "
        f"percentage points, abstention by "
        f"{comparison['end_to_end']['abstention_correctness_percentage']['absolute_change_candidate_minus_baseline']:+.6f} "
        f"percentage points, citation validity by "
        f"{comparison['end_to_end']['citation_validity_percentage']['absolute_change_candidate_minus_baseline']:+.6f} "
        f"percentage points, and p95 latency by "
        f"{comparison['end_to_end']['latency_p95_seconds']['absolute_change_candidate_minus_baseline']:+.6f} "
        "seconds. The direction and magnitude are observations, not claims of "
        "improvement; corpus composition and live LLM behavior are the primary "
        "controlled inputs recorded in the source runs."
    )


def run(
    *,
    baseline_path: Path,
    post_path: Path,
    ingestion_path: Path,
    output_root: Path,
    name: str,
) -> Path:
    baseline = read_json(baseline_path)
    post = read_json(post_path)
    validate_part_b_summary(baseline, baseline_path)
    validate_part_b_summary(post, post_path)
    ingestion_payload = read_json(ingestion_path)
    ingestion = ingestion_facts(ingestion_payload)
    comparison = compare_summaries(baseline, post)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = output_root / f"drift-{name}-{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=False)
    result = {
        "metadata": {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "baseline_summary": str(baseline_path.resolve()),
            "post_batch_b_summary": str(post_path.resolve()),
            "ingestion_evidence": str(ingestion_path.resolve()),
        },
        "ingestion": ingestion,
        "comparison": comparison,
        "interpretation": interpret(comparison, ingestion),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "summary.md").write_text(report_markdown(result), encoding="utf-8")
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare Part B metrics before and after Batch B drift"
    )
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--post-batch-b", type=Path, required=True)
    parser.add_argument("--ingestion-evidence", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--name", default="part-e")
    args = parser.parse_args()
    try:
        output_dir = run(
            baseline_path=args.baseline,
            post_path=args.post_batch_b,
            ingestion_path=args.ingestion_evidence,
            output_root=args.out_root,
            name=args.name,
        )
    except (DriftComparisonError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps({"status": "ok", "output_dir": str(output_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
