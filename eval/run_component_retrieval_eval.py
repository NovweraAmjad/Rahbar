#!/usr/bin/env python3
"""Run baseline and controlled-typo component-level retrieval evaluation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import run_eval  # noqa: E402


METRICS = ("recall_at_3", "recall_at_5", "mrr", "ndcg_at_5")


def metric_delta(baseline: float | None, noisy: float | None) -> float | None:
    if baseline is None or noisy is None:
        return None
    return noisy - baseline


def run(
    *,
    api: str,
    host: str,
    golden: Path,
    output_root: Path,
    timeout: float,
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = output_root / f"component-retrieval-{timestamp}"
    if output_dir.exists():
        raise RuntimeError(f"refusing to overwrite existing output: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=False)

    baseline_path = output_dir / "baseline.json"
    noisy_path = output_dir / "controlled-typos.json"

    baseline_exit = run_eval.main([
        "--api", api,
        "--host", host,
        "--golden", str(golden),
        "--out", str(baseline_path),
        "--baseline", str(output_dir / "baseline-snapshot.json"),
        "--timeout", str(timeout),
        "--noise", "none",
    ])
    if not baseline_path.exists():
        raise RuntimeError(
            f"baseline retrieval evaluation did not write results "
            f"(exit={baseline_exit})"
        )

    noisy_exit = run_eval.main([
        "--api", api,
        "--host", host,
        "--golden", str(golden),
        "--out", str(noisy_path),
        "--baseline", str(output_dir / "unused-noise-baseline.json"),
        "--timeout", str(timeout),
        "--noise", "controlled-typos",
    ])
    if not noisy_path.exists():
        raise RuntimeError(
            f"controlled-typo retrieval evaluation did not write results "
            f"(exit={noisy_exit})"
        )

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    noisy = json.loads(noisy_path.read_text(encoding="utf-8"))

    baseline_metrics = baseline["metrics"]
    noisy_metrics = noisy["metrics"]
    changes = {
        metric: {
            "baseline": baseline_metrics[metric],
            "noisy": noisy_metrics[metric],
            "absolute_change": metric_delta(baseline_metrics[metric], noisy_metrics[metric]),
        }
        for metric in METRICS
    }
    summary = {
        "evaluation_timestamp": datetime.now(timezone.utc).isoformat(),
        "noise_method": "controlled-typos",
        "noise_description": "A deterministic adjacent-character swap is applied to a pair of alphabetic characters in every evaluated question.",
        "baseline_golden_file": str(golden.resolve()),
        "api_base_url": baseline["api_base_url"],
        "metrics": changes,
        "baseline_request_failures": baseline_metrics["request_failures"],
        "noisy_request_failures": noisy_metrics["request_failures"],
        "baseline_answerable_questions": baseline_metrics["answerable_questions"],
        "noisy_answerable_questions": noisy_metrics["answerable_questions"],
        "baseline_result": str(baseline_path),
        "noisy_result": str(noisy_path),
    }
    (output_dir / "comparison.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run baseline and typo-noise component retrieval evaluation"
    )
    parser.add_argument("--api", default="http://localhost:8080")
    parser.add_argument("--host", default="rahbar.local")
    parser.add_argument("--golden", type=Path, default=HERE / "golden.jsonl")
    parser.add_argument("--out-root", type=Path, default=HERE / "results")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    try:
        output_dir = run(
            api=args.api,
            host=args.host,
            golden=args.golden,
            output_root=args.out_root,
            timeout=args.timeout,
        )
    except (RuntimeError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps({"status": "ok", "output_dir": str(output_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
