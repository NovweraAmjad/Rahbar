#!/usr/bin/env python3
"""Run and compare the complete Assignment 03 Part B suite twice."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import random
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "api") not in sys.path:
    sys.path.insert(0, str(ROOT / "api"))

from app.config import get_settings  # noqa: E402
from eval import golden_schema  # noqa: E402
from eval import run_component_retrieval_eval as component_eval  # noqa: E402
from eval import run_end_to_end_eval as end_to_end_eval  # noqa: E402
from eval import run_module_embedding_eval as module_eval  # noqa: E402


FIXED_SEED = 20261121


def validate_api_image_reference(reference: str) -> str:
    prefix, separator, digest = reference.partition("@sha256:")
    if not separator or not prefix:
        raise ValueError("API image must be an immutable image reference ending in @sha256:<64-hex-digest>")
    if len(digest) != 64 or any(
        character not in "0123456789abcdefABCDEF" for character in digest
    ):
        raise ValueError("API image digest must contain 64 hexadecimal characters")
    return reference


def configure_seeds() -> dict[str, object]:
    hash_seed = os.environ.get("PYTHONHASHSEED")
    if hash_seed != str(FIXED_SEED):
        raise RuntimeError(
            f"launch the suite with PYTHONHASHSEED={FIXED_SEED}; "
            "setting it after interpreter startup does not fix Python hashing"
        )
    random.seed(FIXED_SEED)
    try:
        import numpy as np
    except ImportError:
        numpy_seed = None
    else:
        np.random.seed(FIXED_SEED)
        numpy_seed = FIXED_SEED
    return {
        "python_random": FIXED_SEED,
        "python_hash_seed": int(hash_seed),
        "numpy": numpy_seed,
        "dataset": FIXED_SEED,
    }


def absolute_difference(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    if not math.isfinite(left) or not math.isfinite(right):
        return None
    return round(abs(right - left), 12)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_run_metrics(summary: dict, expected_questions: int, run_number: int) -> None:
    """Reject partial suite runs instead of reporting null metrics as a completed run."""
    required = {
        "module": (
            "mean_paraphrase_cosine",
            "mean_unrelated_cosine",
            "paraphrase_minus_unrelated",
            "paraphrase_pair_count",
            "unrelated_pair_count",
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
            "baseline_request_failures",
            "typo_request_failures",
        ),
        "end_to_end": (
            "faithfulness_percentage",
            "abstention_correctness_percentage",
            "citation_validity_percentage",
            "latency_p50_seconds",
            "latency_p95_seconds",
        ),
    }
    for level, metrics in required.items():
        values = summary.get(level)
        if not isinstance(values, dict):
            raise RuntimeError(f"run {run_number} is missing {level} metrics")
        for metric in metrics:
            value = values.get(metric)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise RuntimeError(
                    f"run {run_number} has unavailable or invalid "
                    f"{level}.{metric}"
                )

    module = summary["module"]
    if (
        module["paraphrase_pair_count"] < 15
        or module["unrelated_pair_count"] < 15
    ):
        raise RuntimeError(f"run {run_number} used fewer than 15 pairs in a module category")

    component = summary["component"]
    if component["baseline_request_failures"] or component["typo_request_failures"]:
        raise RuntimeError(
            f"run {run_number} has failed component requests: "
            f"{component['baseline_request_failures']} baseline, "
            f"{component['typo_request_failures']} typo"
        )

    end_to_end = summary["end_to_end"]
    if (
        end_to_end.get("successful_requests") != expected_questions
        or end_to_end.get("request_failures") != 0
    ):
        raise RuntimeError(
            f"run {run_number} did not complete all end-to-end requests: "
            f"{end_to_end.get('successful_requests')} successful, "
            f"{end_to_end.get('request_failures')} failed"
        )


def flatten_module(summary: dict) -> dict[str, float | int | None]:
    metrics = summary["metrics"]
    return {
        "mean_paraphrase_cosine": metrics["mean_paraphrase_cosine"],
        "mean_unrelated_cosine": metrics["mean_unrelated_cosine"],
        "paraphrase_minus_unrelated": metrics["paraphrase_minus_unrelated"],
        "paraphrase_pair_count": metrics["paraphrase_pair_count"],
        "unrelated_pair_count": metrics["unrelated_pair_count"],
        "embedding_time_mean_seconds": metrics["embedding_time_per_chunk_mean_seconds"],
        "embedding_time_p95_seconds": metrics["embedding_time_per_chunk_p95_seconds"],
        "largest_determinism_difference": metrics["largest_determinism_difference"],
        "embedding_dimension": metrics["embedding_dimension"],
    }


def flatten_component(comparison: dict) -> dict[str, float | None]:
    metrics = comparison["metrics"]
    return {
        "baseline_recall_at_3": metrics["recall_at_3"]["baseline"],
        "baseline_recall_at_5": metrics["recall_at_5"]["baseline"],
        "baseline_mrr": metrics["mrr"]["baseline"],
        "baseline_ndcg_at_5": metrics["ndcg_at_5"]["baseline"],
        "typo_recall_at_3": metrics["recall_at_3"]["noisy"],
        "typo_recall_at_5": metrics["recall_at_5"]["noisy"],
        "typo_mrr": metrics["mrr"]["noisy"],
        "typo_ndcg_at_5": metrics["ndcg_at_5"]["noisy"],
        "baseline_request_failures": comparison["baseline_request_failures"],
        "typo_request_failures": comparison["noisy_request_failures"],
    }


def flatten_end_to_end(summary: dict) -> dict[str, float | int | None]:
    metrics = summary.get("metrics", summary)
    return {
        "faithfulness_percentage": metrics["faithfulness"]["percentage"],
        "abstention_correctness_percentage": metrics["abstention_correctness"]["percentage"],
        "citation_validity_percentage": metrics["citation_validity"]["percentage"],
        "latency_p50_seconds": metrics["latency"]["p50_seconds"],
        "latency_p95_seconds": metrics["latency"]["p95_seconds"],
        "successful_requests": metrics["successful_requests"],
        "request_failures": metrics["request_failures"],
    }


def compare_metrics(
    first: dict[str, dict[str, float | int | None]],
    second: dict[str, dict[str, float | int | None]],
) -> dict[str, dict[str, dict[str, float | int | None]]]:
    compared: dict[str, dict[str, dict[str, float | int | None]]] = {}
    for level in sorted(set(first) | set(second)):
        compared[level] = {}
        names = sorted(set(first.get(level, {})) | set(second.get(level, {})))
        for name in names:
            left = first[level].get(name)
            right = second[level].get(name)
            compared[level][name] = {
                "run_1": left,
                "run_2": right,
                "absolute_difference": absolute_difference(
                    left if isinstance(left, (int, float)) else None,
                    right if isinstance(right, (int, float)) else None,
                ),
            }
    return compared


def markdown_table(
    comparison: dict[str, dict[str, dict[str, float | int | None]]],
) -> str:
    lines = [
        "| Level | Metric | Run 1 | Run 2 | Absolute difference |",
        "|---|---|---:|---:|---:|",
    ]
    for level in ("module", "component", "end_to_end"):
        for name in sorted(comparison.get(level, {})):
            values = comparison[level][name]
            def format_value(value: float | int | None) -> str:
                if value is None:
                    return "unavailable"
                if isinstance(value, float):
                    return f"{value:.6f}"
                return str(value)

            lines.append(
                f"| {level} | {name} | "
                f"{format_value(values['run_1'])} | "
                f"{format_value(values['run_2'])} | "
                f"{format_value(values['absolute_difference'])} |"
            )
    return "\n".join(lines) + "\n"


def run(
    *,
    api: str,
    api_image: str,
    host: str,
    golden: Path,
    output_root: Path,
    ollama_url: str,
    embedding_model: str,
) -> Path:
    api_image = validate_api_image_reference(api_image)
    seeds = configure_seeds()
    golden_questions = golden_schema.load_golden_jsonl(golden)
    dataset_counts = {
        "total": len(golden_questions),
        "answerable": sum(
            question["expected_answerability"] == "answerable"
            for question in golden_questions
        ),
        "unanswerable": sum(
            question["expected_answerability"] == "must_abstain"
            for question in golden_questions
        ),
        "hard": sum(question["category"] == "hard" for question in golden_questions),
        "human": sum(question["origin"] == "human" for question in golden_questions),
        "synthetic": sum(
            question["origin"] == "synthetic" for question in golden_questions
        ),
        "reviewed_synthetic": sum(
            question["origin"] == "synthetic"
            and question["manual_review"]["status"] == "reviewed"
            for question in golden_questions
        ),
    }
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suite_dir = output_root / f"part-b-stochasticity-{timestamp}"
    if suite_dir.exists():
        raise RuntimeError(f"refusing to overwrite existing result: {suite_dir}")
    suite_dir.mkdir(parents=True, exist_ok=False)
    settings = get_settings()
    expected_question_count = dataset_counts["total"]

    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "golden_file": str(golden.resolve()),
        "api_base_url": api,
        "api_image_reference": api_image,
        "host": host,
        "embedding_model": embedding_model,
        "embedding_ollama_url": ollama_url,
        "llm_model": os.environ.get("LLM_MODEL", settings.llm_model),
        "llm_temperature": float(os.environ.get("TEMPERATURE", settings.temperature)),
        "seeds": seeds,
        "golden_dataset_counts": dataset_counts,
        "faithfulness_assessment": {
            "method": "conservative_citation_context_check",
            "uses_llm_judge": False,
            "limitation": (
                "Checks citation provenance against retrieved context, not semantic "
                "entailment of every answer claim."
            ),
        },
        "remaining_variation": (
            "The module embedding path uses fixed dataset and provider settings. "
            "Component retrieval is deterministic when the live corpus and returned "
            "rankings are unchanged. End-to-end LLM text may vary because the provider "
            "uses temperature 0.2; provider restarts, cache behavior, model revisions, "
            "request failures, and live retrieval state can also affect results."
        ),
    }

    flattened_runs: dict[str, dict[str, dict[str, float | int | None]]] = {}
    run_paths: dict[str, str] = {}
    for run_number in (1, 2):
        configure_seeds()
        run_dir = suite_dir / f"run-{run_number}"
        run_dir.mkdir(parents=True, exist_ok=False)

        module_dir = module_eval.run_evaluation(
            pairs_path=HERE / "module-embedding-pairs.yaml",
            output_root=run_dir / "module",
            ollama_url=ollama_url,
            model=embedding_model,
        )

        component_dir = component_eval.run(
            api=api,
            host=host,
            golden=golden,
            output_root=run_dir / "component",
            timeout=120.0,
        )

        end_dir = end_to_end_eval.run_evaluation(
            api=api,
            host=host,
            golden=golden,
            timeout=120.0,
            output_root=run_dir / "end-to-end",
        )

        module_summary = read_json(module_dir / "summary.json")
        component_comparison = read_json(component_dir / "comparison.json")
        end_summary = read_json(end_dir / "summary.json")
        flattened_runs[str(run_number)] = {
            "module": flatten_module(module_summary),
            "component": flatten_component(component_comparison),
            "end_to_end": flatten_end_to_end(end_summary),
        }
        validate_run_metrics(
            flattened_runs[str(run_number)],
            expected_question_count,
            run_number,
        )
        run_paths[str(run_number)] = {
            "module": str(module_dir),
            "component": str(component_dir),
            "end_to_end": str(end_dir),
        }

    comparison = compare_metrics(
        flattened_runs["1"],
        flattened_runs["2"],
    )
    result = {
        "metadata": metadata,
        "run_paths": run_paths,
        "runs": flattened_runs,
        "comparison": comparison,
    }
    (suite_dir / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (suite_dir / "summary.md").write_text(
        "# Assignment 03 Part B stochasticity summary\n\n"
        + markdown_table(comparison)
        + "\n## Remaining variation\n\n"
        + metadata["remaining_variation"]
        + "\n",
        encoding="utf-8",
    )
    return suite_dir


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Run the complete Assignment 03 Part B suite twice"
    )
    parser.add_argument("--api", default="http://localhost:8080")
    parser.add_argument(
        "--api-image",
        required=True,
        help="immutable API image reference reported by the running pod (repository@sha256:digest)",
    )
    parser.add_argument("--host", default="rahbar.local")
    parser.add_argument("--golden", type=Path, default=HERE / "golden.jsonl")
    parser.add_argument("--out-root", type=Path, default=HERE / "results")
    parser.add_argument("--ollama-url", default="http://172.23.112.1:11434")
    parser.add_argument("--embedding-model", default=settings.embed_model)
    args = parser.parse_args()
    try:
        suite_dir = run(
            api=args.api,
            api_image=args.api_image,
            host=args.host,
            golden=args.golden,
            output_root=args.out_root,
            ollama_url=args.ollama_url,
            embedding_model=args.embedding_model,
        )
    except (RuntimeError, OSError, ValueError, module_eval.ModuleEmbeddingError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps({"status": "ok", "output_dir": str(suite_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
