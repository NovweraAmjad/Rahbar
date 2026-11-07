#!/usr/bin/env python3
"""Measure semantic-cache cosine thresholds with configured Ollama embeddings."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Any

import httpx
import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from app.config import Settings  # noqa: E402
from app.embeddings import embed_query  # noqa: E402
from app.services.semantic_cache import _cosine_similarity  # noqa: E402

EVAL_DIR = ROOT / "eval"
DEFAULT_PAIRS = EVAL_DIR / "cache-threshold-pairs.yaml"
DEFAULT_OUTPUT_ROOT = EVAL_DIR / "cache-threshold-runs"
DEFAULT_OLLAMA_URL = "http://172.23.112.1:11434"
DEFAULT_EMBED_MODEL = "nomic-embed-text"
PRODUCTION_THRESHOLD = 0.95
ALLOWED_CATEGORIES = {
    "equivalent",
    "paraphrase",
    "hard_negative",
    "version_sensitive",
    "entity_sensitive",
    "unrelated",
}


class CacheThresholdError(RuntimeError):
    """Raised when the cache-threshold experiment cannot complete safely."""


@dataclass(frozen=True)
class QueryPair:
    id: str
    category: str
    expected_reuse: bool
    cached_question: str
    query_question: str
    rationale: str


@dataclass(frozen=True)
class Experiment:
    thresholds: tuple[float, ...]
    pairs: tuple[QueryPair, ...]


def load_experiment(path: Path = DEFAULT_PAIRS) -> Experiment:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CacheThresholdError(f"cannot load pair dataset {path}: {exc}") from exc

    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise CacheThresholdError("pair dataset must use schema_version 1")
    raw_thresholds = raw.get("thresholds")
    if not isinstance(raw_thresholds, list) or len(raw_thresholds) < 3:
        raise CacheThresholdError("pair dataset must define at least three thresholds")

    thresholds: list[float] = []
    for value in raw_thresholds:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CacheThresholdError("thresholds must be numeric")
        threshold = float(value)
        if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
            raise CacheThresholdError("thresholds must be finite values from 0.0 to 1.0")
        if threshold in thresholds:
            raise CacheThresholdError(f"duplicate threshold: {threshold}")
        thresholds.append(threshold)
    if PRODUCTION_THRESHOLD not in thresholds:
        raise CacheThresholdError(
            f"threshold list must include production threshold {PRODUCTION_THRESHOLD}"
        )

    raw_pairs = raw.get("pairs")
    if not isinstance(raw_pairs, list) or not raw_pairs:
        raise CacheThresholdError("pair dataset must contain a non-empty pairs list")

    pairs: list[QueryPair] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_pairs, start=1):
        if not isinstance(item, dict):
            raise CacheThresholdError(f"pair {index} must be a mapping")
        pair_id = item.get("id")
        category = item.get("category")
        expected = item.get("expected_reuse")
        cached_question = item.get("cached_question")
        query_question = item.get("query_question")
        rationale = item.get("rationale")
        if not isinstance(pair_id, str) or not pair_id.strip() or pair_id in seen_ids:
            raise CacheThresholdError(f"pair {index} needs a unique non-empty id")
        if category not in ALLOWED_CATEGORIES:
            raise CacheThresholdError(f"{pair_id}: unsupported category {category!r}")
        if not isinstance(expected, bool):
            raise CacheThresholdError(f"{pair_id}: expected_reuse must be a boolean")
        for field_name, value in (
            ("cached_question", cached_question),
            ("query_question", query_question),
            ("rationale", rationale),
        ):
            if not isinstance(value, str) or not value.strip():
                raise CacheThresholdError(f"{pair_id}: {field_name} must be non-empty text")
        assert isinstance(cached_question, str)
        assert isinstance(query_question, str)
        assert isinstance(rationale, str)
        if cached_question.strip() == query_question.strip() and category != "equivalent":
            raise CacheThresholdError(
                f"{pair_id}: identical questions must be labeled equivalent"
            )
        seen_ids.add(pair_id)
        pairs.append(
            QueryPair(
                id=pair_id.strip(),
                category=category,
                expected_reuse=expected,
                cached_question=cached_question.strip(),
                query_question=query_question.strip(),
                rationale=rationale.strip(),
            )
        )

    if not any(pair.category == "hard_negative" and not pair.expected_reuse for pair in pairs):
        raise CacheThresholdError("dataset must include explicitly labeled hard negatives")
    return Experiment(tuple(thresholds), tuple(pairs))


def threshold_decision(similarity: float, threshold: float) -> bool:
    if not math.isfinite(similarity) or not math.isfinite(threshold):
        raise CacheThresholdError("similarity and threshold must be finite")
    return similarity >= threshold


def _percentage(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(100.0 * numerator / denominator, 4)


def summarize_threshold(
    threshold: float,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    true_hits = sum(row["expected_reuse"] and row["actual_reuse"] for row in rows)
    false_hits = sum(not row["expected_reuse"] and row["actual_reuse"] for row in rows)
    missed_hits = sum(row["expected_reuse"] and not row["actual_reuse"] for row in rows)
    true_misses = sum(not row["expected_reuse"] and not row["actual_reuse"] for row in rows)
    predicted_hits = true_hits + false_hits
    positives = true_hits + missed_hits
    negatives = false_hits + true_misses
    return {
        "threshold": threshold,
        "pair_count": len(rows),
        "true_hits": true_hits,
        "false_hits": false_hits,
        "missed_hits": missed_hits,
        "true_misses": true_misses,
        "hit_rate_percentage": _percentage(predicted_hits, len(rows)),
        "precision_percentage": _percentage(true_hits, predicted_hits),
        "positive_pair_recall_percentage": _percentage(true_hits, positives),
        "false_hit_rate_percentage": _percentage(false_hits, negatives),
        "positive_pair_count": positives,
        "negative_pair_count": negatives,
    }


def evaluate_vectors(
    experiment: Experiment,
    embeddings: dict[str, list[float]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    dimensions = {len(vector) for vector in embeddings.values()}
    if len(dimensions) != 1 or not dimensions or 0 in dimensions:
        raise CacheThresholdError("embedding vectors have inconsistent or empty dimensions")

    pair_rows: list[dict[str, Any]] = []
    for pair in experiment.pairs:
        similarity = _cosine_similarity(
            embeddings[pair.cached_question],
            embeddings[pair.query_question],
        )
        if similarity is None:
            raise CacheThresholdError(f"{pair.id}: production cosine calculation was undefined")
        decisions = [
            {
                "threshold": threshold,
                "actual_reuse": threshold_decision(similarity, threshold),
                "outcome": (
                    "true_hit"
                    if pair.expected_reuse and threshold_decision(similarity, threshold)
                    else "missed_hit"
                    if pair.expected_reuse
                    else "false_hit"
                    if threshold_decision(similarity, threshold)
                    else "true_miss"
                ),
            }
            for threshold in experiment.thresholds
        ]
        pair_rows.append(
            {
                "id": pair.id,
                "category": pair.category,
                "cached_question": pair.cached_question,
                "query_question": pair.query_question,
                "expected_reuse": pair.expected_reuse,
                "rationale": pair.rationale,
                "cosine_similarity": round(similarity, 8),
                "threshold_decisions": decisions,
            }
        )

    summaries = [
        summarize_threshold(
            threshold,
            [
                {
                    "expected_reuse": row["expected_reuse"],
                    "actual_reuse": decision["actual_reuse"],
                }
                for row in pair_rows
                for decision in row["threshold_decisions"]
                if decision["threshold"] == threshold
            ],
        )
        for threshold in experiment.thresholds
    ]
    return pair_rows, summaries


def _embed_questions(
    experiment: Experiment,
    *,
    ollama_url: str,
    model: str,
) -> dict[str, list[float]]:
    settings = Settings(embed_model=model, ollama_url=ollama_url)
    questions = dict.fromkeys(
        question
        for pair in experiment.pairs
        for question in (pair.cached_question, pair.query_question)
    )
    vectors: dict[str, list[float]] = {}
    for question in questions:
        try:
            vectors[question] = embed_query(question, settings)
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            raise CacheThresholdError(
                f"embedding request failed for a pair question "
                f"({type(exc).__name__}); verify Ollama URL and model {model!r}"
            ) from exc
    return vectors


def _resolve_model_identity(ollama_url: str, model: str) -> dict[str, str]:
    try:
        response = httpx.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=10.0)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise CacheThresholdError(
            f"cannot verify Ollama model identity ({type(exc).__name__})"
        ) from exc
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        raise CacheThresholdError("Ollama returned malformed model tags")

    def canonical(value: Any) -> str:
        name = str(value).rsplit("/", 1)[-1]
        return name if ":" in name else f"{name}:latest"

    identity = next(
        (
            item
            for item in models
            if isinstance(item, dict)
            and canonical(item.get("name", item.get("model", ""))) == canonical(model)
        ),
        None,
    )
    if identity is None:
        raise CacheThresholdError(f"configured Ollama model tag is unavailable: {model!r}")
    resolved_name = identity.get("name", identity.get("model"))
    digest = identity.get("digest")
    if not isinstance(resolved_name, str) or not isinstance(digest, str) or not digest:
        raise CacheThresholdError("Ollama model tags lack a resolved name or digest")
    return {"configured": model, "name": resolved_name, "digest": digest}


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def run_evaluation(
    *,
    output_dir: Path,
    pairs_path: Path = DEFAULT_PAIRS,
    ollama_url: str,
    model: str,
) -> Path:
    if output_dir.exists():
        raise CacheThresholdError(
            f"output directory already exists; refusing to overwrite: {output_dir}"
        )
    experiment = load_experiment(pairs_path)
    model_identity = _resolve_model_identity(ollama_url, model)
    embeddings = _embed_questions(experiment, ollama_url=ollama_url, model=model)
    if _resolve_model_identity(ollama_url, model) != model_identity:
        raise CacheThresholdError("Ollama model identity changed during the evaluation")
    pair_rows, summaries = evaluate_vectors(experiment, embeddings)
    zero_false_hit_thresholds = [
        item["threshold"] for item in summaries if item["false_hits"] == 0
    ]
    output_dir.mkdir(parents=True, exist_ok=False)
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(pairs_path.resolve()),
        "dataset_sha256": _sha256(pairs_path),
        "runner_sha256": _sha256(Path(__file__)),
        "embedding_model": model,
        "embedding_model_identity": model_identity,
        "ollama_url": ollama_url,
        "production_threshold": PRODUCTION_THRESHOLD,
        "thresholds": list(experiment.thresholds),
        "pair_count": len(pair_rows),
        "unique_question_count": len(embeddings),
        "pair_labels_are_explicit": True,
        "decision_semantics": "production cosine similarity >= threshold",
        "isolation_assumptions": {
            "each_pair_is_evaluated_independently": True,
            "one_valid_grounded_cache_entry_is_assumed_per_pair": True,
            "collection_and_generation_are_fixed_within_each_pair": True,
            "cross_pair_entries_are_not_shared": True,
            "redis_operations_and_invalidation_are_not_executed": True,
        },
    }
    _write_json(output_dir / "pair-results.json", {"metadata": metadata, "pairs": pair_rows})
    _write_json(
        output_dir / "summary.json",
        {
            "metadata": metadata,
            "thresholds": summaries,
            "lowest_tested_zero_false_hit_threshold": (
                min(zero_false_hit_thresholds) if zero_false_hit_thresholds else None
            ),
        },
    )
    return output_dir


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=Path, default=DEFAULT_PAIRS)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("EMBED_MODEL", DEFAULT_EMBED_MODEL),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_dir = args.out_dir
    if output_dir is None:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%S%f")
        output_dir = DEFAULT_OUTPUT_ROOT / run_id
    elif not output_dir.is_absolute():
        output_dir = ROOT / output_dir

    try:
        result_dir = run_evaluation(
            output_dir=output_dir,
            pairs_path=args.pairs,
            ollama_url=args.ollama_url,
            model=args.model,
        )
    except (CacheThresholdError, OSError) as exc:
        print(f"cache threshold evaluation failed: {exc}", file=sys.stderr)
        return 1

    summary = json.loads((result_dir / "summary.json").read_text(encoding="utf-8"))
    print(f"Results: {result_dir}")
    for result in summary["thresholds"]:
        print(
            "threshold={threshold:.2f} hits={true_hits} false_hits={false_hits} "
            "missed_hits={missed_hits} hit_rate={hit_rate_percentage}% "
            "positive_pair_recall={positive_pair_recall_percentage}% "
            "precision={precision_percentage}% false_hit_rate={false_hit_rate_percentage}%"
            .format(**result)
        )
    print(
        "Lowest tested threshold with zero false hits: "
        f"{summary['lowest_tested_zero_false_hit_threshold']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
