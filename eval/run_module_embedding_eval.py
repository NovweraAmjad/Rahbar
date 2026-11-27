#!/usr/bin/env python3
"""Run Assignment 03 module-level embedding measurements with RAHBAR's Ollama provider."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import sys
import time
from typing import Any

import httpx
import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from app.config import Settings, get_settings  # noqa: E402
from app.embeddings import embed_query  # noqa: E402

EVAL_DIR = ROOT / "eval"
DEFAULT_PAIRS = EVAL_DIR / "module-embedding-pairs.yaml"
DEFAULT_OUTPUT_ROOT = EVAL_DIR / "results"


class ModuleEmbeddingError(RuntimeError):
    pass


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise ModuleEmbeddingError("cosine vectors must be non-empty and have equal dimensions")
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        raise ModuleEmbeddingError("cosine vectors cannot have zero norm")
    result = dot / (left_norm * right_norm)
    if not math.isfinite(result):
        raise ModuleEmbeddingError("cosine similarity must be finite")
    return result


def mean(values: list[float]) -> float:
    if not values:
        raise ModuleEmbeddingError("cannot calculate mean of an empty list")
    return sum(values) / len(values)


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        raise ModuleEmbeddingError("cannot calculate percentile of an empty list")
    if not 0.0 <= percentile_value <= 100.0:
        raise ModuleEmbeddingError("percentile must be from 0 to 100")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile_value / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def load_pairs(path: Path) -> tuple[int, list[dict[str, str]]]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ModuleEmbeddingError(f"cannot load pair dataset {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ModuleEmbeddingError("pair dataset must use schema_version 1")
    seed = raw.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ModuleEmbeddingError("pair dataset must contain an integer seed")
    raw_pairs = raw.get("pairs")
    if not isinstance(raw_pairs, list):
        raise ModuleEmbeddingError("pair dataset must contain a pairs list")

    pairs: list[dict[str, str]] = []
    ids: set[str] = set()
    for index, item in enumerate(raw_pairs, 1):
        if not isinstance(item, dict):
            raise ModuleEmbeddingError(f"pair {index} must be a mapping")
        pair_id = item.get("id")
        category = item.get("category")
        left = item.get("left")
        right = item.get("right")
        if not isinstance(pair_id, str) or not pair_id.strip() or pair_id in ids:
            raise ModuleEmbeddingError(f"pair {index} needs a unique non-empty id")
        if category not in {"paraphrase", "unrelated"}:
            raise ModuleEmbeddingError(f"{pair_id}: category must be paraphrase or unrelated")
        if not isinstance(left, str) or not left.strip():
            raise ModuleEmbeddingError(f"{pair_id}: left text is required")
        if not isinstance(right, str) or not right.strip():
            raise ModuleEmbeddingError(f"{pair_id}: right text is required")
        ids.add(pair_id)
        pairs.append(
            {
                "id": pair_id,
                "category": category,
                "left": left,
                "right": right,
            }
        )

    category_counts = defaultdict(int)
    for pair in pairs:
        category_counts[pair["category"]] += 1
    if category_counts["paraphrase"] < 15:
        raise ModuleEmbeddingError("at least 15 paraphrase pairs are required")
    if category_counts["unrelated"] < 15:
        raise ModuleEmbeddingError("at least 15 unrelated pairs are required")
    return seed, pairs


def resolve_model_identity(ollama_url: str, model: str) -> dict[str, str]:
    try:
        response = httpx.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=10.0)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ModuleEmbeddingError(
            f"cannot verify Ollama model identity ({type(exc).__name__})"
        ) from exc
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        raise ModuleEmbeddingError("Ollama returned malformed model tags")

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
        raise ModuleEmbeddingError(
            f"configured Ollama model tag is unavailable: {model!r}"
        )
    resolved_name = identity.get("name", identity.get("model"))
    digest = identity.get("digest")
    if not isinstance(resolved_name, str) or not isinstance(digest, str) or not digest:
        raise ModuleEmbeddingError("Ollama model tags lack a resolved name or digest")
    return {"configured": model, "name": resolved_name, "digest": digest}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def calculate_metrics(
    pairs: list[dict[str, str]],
    vectors: dict[str, list[float]],
) -> tuple[dict[str, float | int], list[dict[str, Any]]]:
    dimensions = {len(vector) for vector in vectors.values()}
    if len(dimensions) != 1 or 0 in dimensions:
        raise ModuleEmbeddingError("vectors have inconsistent or empty dimensions")

    category_values: dict[str, list[float]] = {
        "paraphrase": [],
        "unrelated": [],
    }
    rows: list[dict[str, Any]] = []
    for pair in pairs:
        similarity = cosine_similarity(
            vectors[pair["left"]],
            vectors[pair["right"]],
        )
        category_values[pair["category"]].append(similarity)
        rows.append(
            {
                "id": pair["id"],
                "category": pair["category"],
                "left": pair["left"],
                "right": pair["right"],
                "cosine_similarity": similarity,
            }
        )

    paraphrase_mean = mean(category_values["paraphrase"])
    unrelated_mean = mean(category_values["unrelated"])
    return {
        "embedding_dimension": next(iter(dimensions)),
        "mean_paraphrase_cosine": paraphrase_mean,
        "mean_unrelated_cosine": unrelated_mean,
        "paraphrase_minus_unrelated": paraphrase_mean - unrelated_mean,
        "paraphrase_pair_count": len(category_values["paraphrase"]),
        "unrelated_pair_count": len(category_values["unrelated"]),
    }, rows


def largest_determinism_difference(
    first: dict[str, list[float]],
    second: dict[str, list[float]],
) -> float:
    if set(first) != set(second):
        raise ModuleEmbeddingError("determinism runs used different text sets")
    largest = 0.0
    for text in first:
        if len(first[text]) != len(second[text]):
            raise ModuleEmbeddingError("determinism vectors have inconsistent dimensions")
        for left, right in zip(first[text], second[text]):
            largest = max(largest, abs(left - right))
    return largest


def run_evaluation(
    *,
    pairs_path: Path,
    output_root: Path,
    ollama_url: str,
    model: str,
) -> Path:
    if output_root.exists() and not output_root.is_dir():
        raise ModuleEmbeddingError("output root must be a directory")
    seed, pairs = load_pairs(pairs_path)
    random.seed(seed)
    model_identity = resolve_model_identity(ollama_url, model)

    settings = Settings(embed_model=model, ollama_url=ollama_url)
    unique_texts = list(dict.fromkeys(text for pair in pairs for text in (pair["left"], pair["right"])))

    vectors: dict[str, list[float]] = {}
    timing_seconds: list[float] = []
    timing_rows: list[dict[str, Any]] = []
    for text in unique_texts:
        started = time.perf_counter()
        try:
            vector = embed_query(text, settings)
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            raise ModuleEmbeddingError(
                f"embedding request failed ({type(exc).__name__}); "
                f"verify Ollama URL and model {model!r}"
            ) from exc
        elapsed = time.perf_counter() - started
        vectors[text] = vector
        timing_seconds.append(elapsed)
        timing_rows.append(
            {
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "embedding_seconds": elapsed,
            }
        )

    repeated: dict[str, list[float]] = {}
    for text in unique_texts:
        try:
            repeated[text] = embed_query(text, settings)
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            raise ModuleEmbeddingError(
                f"determinism embedding request failed ({type(exc).__name__})"
            ) from exc

    if resolve_model_identity(ollama_url, model) != model_identity:
        raise ModuleEmbeddingError("Ollama model identity changed during evaluation")

    metrics, pair_rows = calculate_metrics(pairs, vectors)
    determinism_difference = largest_determinism_difference(vectors, repeated)
    generated_at = datetime.now(timezone.utc)
    timestamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    output_dir = output_root / f"module-embedding-{timestamp}"
    if output_dir.exists():
        raise ModuleEmbeddingError(f"refusing to overwrite existing output: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=False)

    embedding_dimension = int(metrics["embedding_dimension"])
    timing_mean = mean(timing_seconds)
    metadata = {
        "generated_at": generated_at.isoformat(),
        "fixed_seed": seed,
        "dataset": str(pairs_path.resolve()),
        "dataset_sha256": sha256_file(pairs_path),
        "runner_sha256": sha256_file(Path(__file__)),
        "provider": "Ollama",
        "ollama_url": ollama_url,
        "embedding_model": model,
        "embedding_model_identity": model_identity,
        "embedding_dimensions": embedding_dimension,
        "unique_chunk_count": len(unique_texts),
        "timing_method": "time.perf_counter() around each production embed_query(text, Settings) call",
        "metric_definitions": {
            "cosine_similarity": "dot(a,b)/(||a||*||b||)",
            "mean_paraphrase_cosine": "mean cosine across 15+ labeled paraphrase pairs",
            "mean_unrelated_cosine": "mean cosine across 15+ labeled unrelated pairs",
            "paraphrase_minus_unrelated": "mean_paraphrase_cosine minus mean_unrelated_cosine",
            "embedding_time_per_chunk": "one production Ollama embedding request for each unique text",
            "largest_determinism_difference": "largest absolute component difference across identical text embedded twice",
        },
    }
    summary = {
        "metadata": metadata,
        "metrics": {
            **metrics,
            "embedding_dimension": embedding_dimension,
            "embedding_time_per_chunk_mean_seconds": timing_mean,
            "embedding_time_per_chunk_median_seconds": percentile(timing_seconds, 50.0),
            "embedding_time_per_chunk_p95_seconds": percentile(timing_seconds, 95.0),
            "largest_determinism_difference": determinism_difference,
            "determinism_comparison": "same text embedded twice using the production provider",
        },
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "pair-results.json").write_text(
        json.dumps(
            {"metadata": metadata, "pairs": pair_rows},
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ) + "\n",
        encoding="utf-8",
    )
    (output_dir / "timing-results.json").write_text(
        json.dumps(
            {"metadata": metadata, "timings": timing_rows},
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ) + "\n",
        encoding="utf-8",
    )
    return output_dir


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Measure RAHBAR's configured Ollama embedding provider"
    )
    parser.add_argument("--pairs", type=Path, default=DEFAULT_PAIRS)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--ollama-url", default=settings.ollama_url)
    parser.add_argument("--model", default=settings.embed_model)
    args = parser.parse_args()
    try:
        output_dir = run_evaluation(
            pairs_path=args.pairs,
            output_root=args.out_root,
            ollama_url=args.ollama_url,
            model=args.model,
        )
    except ModuleEmbeddingError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps({"status": "ok", "output_dir": str(output_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
