#!/usr/bin/env python3
"""Measure live-query coverage against the golden set with RAHBAR's real embedder."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import yaml  # type: ignore[import-untyped]

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for path in (ROOT, ROOT / "api"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.config import Settings, get_settings  # noqa: E402
from app.embeddings import embed_query  # noqa: E402
from eval import golden_schema  # noqa: E402
from eval.run_module_embedding_eval import (  # noqa: E402
    ModuleEmbeddingError,
    cosine_similarity,
    resolve_model_identity,
)

DEFAULT_QUERIES = HERE / "live_queries.yaml"
DEFAULT_GOLDEN = HERE / "golden.jsonl"
DEFAULT_OUTPUT_ROOT = HERE / "results"
DEFAULT_SIMILARITY_THRESHOLD = 0.80
DEFAULT_MINIMUM_COVERAGE = 0.85

THRESHOLD_RATIONALE = (
    "The 0.80 cosine floor is rounded down from the measured 0.8311 mean "
    "similarity for 15 production-provider paraphrase pairs in Part B, while "
    "remaining well above the measured 0.3619 unrelated-pair mean. The 85% "
    "minimum uses the paper's example that coverage below 85% flags a gap."
)


class CoverageEvaluationError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_live_queries(path: Path) -> list[dict[str, Any]]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CoverageEvaluationError(f"cannot read live queries {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise CoverageEvaluationError("live query log must use schema_version 1")
    items = raw.get("queries")
    if not isinstance(items, list):
        raise CoverageEvaluationError("live query log must contain a queries list")

    result: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise CoverageEvaluationError(f"query {index} must be a mapping")
        query_id = item.get("id")
        topic = item.get("topic")
        text = item.get("query")
        golden_domain = item.get("golden_domain")
        if not isinstance(query_id, str) or not query_id.strip() or query_id in ids:
            raise CoverageEvaluationError(f"query {index} needs a unique non-empty id")
        if not isinstance(topic, str) or not topic.strip():
            raise CoverageEvaluationError(f"{query_id}: topic is required")
        if not isinstance(text, str) or not text.strip():
            raise CoverageEvaluationError(f"{query_id}: query text is required")
        if not isinstance(golden_domain, bool):
            raise CoverageEvaluationError(f"{query_id}: golden_domain must be boolean")
        ids.add(query_id)
        result.append(
            {
                "id": query_id,
                "topic": topic,
                "query": text,
                "golden_domain": golden_domain,
            }
        )

    in_domain = sum(item["golden_domain"] for item in result)
    out_of_domain = len(result) - in_domain
    if len(result) < 40 or in_domain < 28 or out_of_domain < 12:
        raise CoverageEvaluationError(
            "live query log requires at least 40 queries, including 28 in the "
            f"golden domain and 12 out of domain; got {len(result)}, "
            f"{in_domain}, and {out_of_domain}"
        )
    return result


def evaluate_matches(
    live_queries: list[dict[str, Any]],
    golden_questions: list[dict[str, Any]],
    live_vectors: dict[str, list[float]],
    golden_vectors: dict[str, list[float]],
    similarity_threshold: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for live in live_queries:
        query_vector = live_vectors[live["id"]]
        nearest: dict[str, Any] | None = None
        for golden in golden_questions:
            score = cosine_similarity(query_vector, golden_vectors[golden["id"]])
            if nearest is None or score > nearest["similarity"]:
                nearest = {
                    "nearest_golden_id": golden["id"],
                    "nearest_golden_question": golden["question"],
                    "nearest_golden_category": golden["category"],
                    "similarity": score,
                }
        if nearest is None:
            raise CoverageEvaluationError("golden set is empty")
        rows.append(
            {
                **live,
                **nearest,
                "covered": bool(nearest["similarity"] >= similarity_threshold),
            }
        )
    return rows


def group_uncovered(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not row["covered"]:
            grouped[row["topic"]].append(
                {
                    "id": row["id"],
                    "query": row["query"],
                    "nearest_golden_id": row["nearest_golden_id"],
                    "nearest_golden_question": row["nearest_golden_question"],
                    "similarity": row["similarity"],
                }
            )
    return dict(sorted(grouped.items()))


def gate_exit_code(coverage_percentage: float, minimum_coverage: float) -> int:
    return 0 if coverage_percentage >= minimum_coverage * 100.0 else 1


def report_markdown(result: dict[str, Any]) -> str:
    summary = result["summary"]
    metadata = result["metadata"]
    lines = [
        "# Assignment 03 Part E query coverage",
        "",
        f"- Generated: {metadata['generated_at']}",
        f"- Embedding model: `{metadata['embedding_model']['name']}`",
        f"- Similarity threshold: {metadata['similarity_threshold']:.2f}",
        f"- Minimum coverage: {metadata['minimum_coverage_percentage']:.2f}%",
        f"- Coverage: **{summary['coverage_percentage']:.2f}%** "
        f"({summary['covered_queries']}/{summary['total_queries']})",
        "",
        "## Threshold justification",
        "",
        metadata["threshold_rationale"],
        "",
        "## All live queries",
        "",
        "| Query | Topic | Covered | Nearest golden | Similarity |",
        "| --- | --- | --- | --- | ---: |",
    ]
    for row in result["matches"]:
        lines.append(
            f"| {row['id']} | {row['topic']} | {'yes' if row['covered'] else 'no'} | "
            f"{row['nearest_golden_id']}: {row['nearest_golden_question'].replace('|', '/')} | "
            f"{row['similarity']:.6f} |"
        )
    lines.extend(["", "## Uncovered queries by topic", ""])
    if not result["uncovered_by_topic"]:
        lines.append("No uncovered topics at the configured threshold.")
    else:
        for topic, rows in result["uncovered_by_topic"].items():
            lines.append(f"### {topic}")
            lines.append("")
            for row in rows:
                lines.append(
                    f"- `{row['id']}` ({row['similarity']:.6f}, nearest "
                    f"`{row['nearest_golden_id']}`): {row['query']}"
                )
            lines.append("")
    comparison = result.get("comparison")
    if comparison:
        lines.extend(
            [
                "## Coverage comparison",
                "",
                f"- Baseline coverage: {comparison['baseline_coverage_percentage']:.2f}%",
                f"- Current coverage: {comparison['current_coverage_percentage']:.2f}%",
                f"- Change: {comparison['coverage_percentage_change']:+.2f} points",
                f"- Baseline result: `{comparison['baseline_result']}`",
            ]
        )
    return "\n".join(lines) + "\n"


def read_baseline(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CoverageEvaluationError(f"cannot read baseline coverage {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("summary"), dict):
        raise CoverageEvaluationError(f"invalid baseline coverage result: {path}")
    return payload


def run(
    *,
    queries_path: Path,
    golden_path: Path,
    output_root: Path,
    name: str,
    ollama_url: str,
    model: str,
    similarity_threshold: float,
    minimum_coverage: float,
    baseline_path: Path | None,
) -> tuple[Path, dict[str, Any]]:
    if not 0.0 < similarity_threshold <= 1.0:
        raise CoverageEvaluationError("similarity threshold must be in (0, 1]")
    if not 0.0 < minimum_coverage <= 1.0:
        raise CoverageEvaluationError("minimum coverage must be in (0, 1]")
    queries = load_live_queries(queries_path)
    golden = golden_schema.load_golden_jsonl(golden_path)
    baseline = read_baseline(baseline_path)
    model_identity = resolve_model_identity(ollama_url, model)
    settings = Settings(embed_model=model, ollama_url=ollama_url)

    live_vectors = {
        item["id"]: embed_query(item["query"], settings)
        for item in queries
    }
    golden_vectors = {
        item["id"]: embed_query(item["question"], settings)
        for item in golden
    }
    matches = evaluate_matches(
        queries,
        golden,
        live_vectors,
        golden_vectors,
        similarity_threshold,
    )
    covered = sum(item["covered"] for item in matches)
    coverage_percentage = 100.0 * covered / len(matches)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = output_root / f"coverage-{name}-{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=False)
    result: dict[str, Any] = {
        "metadata": {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "queries_file": str(queries_path.resolve()),
            "queries_sha256": sha256_file(queries_path),
            "golden_file": str(golden_path.resolve()),
            "golden_sha256": sha256_file(golden_path),
            "embedding_ollama_url": ollama_url,
            "embedding_model": model_identity,
            "similarity_threshold": similarity_threshold,
            "minimum_coverage_percentage": minimum_coverage * 100.0,
            "threshold_rationale": THRESHOLD_RATIONALE,
        },
        "summary": {
            "total_queries": len(matches),
            "covered_queries": covered,
            "uncovered_queries": len(matches) - covered,
            "coverage_percentage": coverage_percentage,
            "gate_passed": gate_exit_code(
                coverage_percentage, minimum_coverage
            )
            == 0,
            "in_domain_queries": sum(item["golden_domain"] for item in queries),
            "out_of_domain_queries": sum(
                not item["golden_domain"] for item in queries
            ),
        },
        "matches": matches,
        "uncovered_by_topic": group_uncovered(matches),
    }
    if baseline:
        baseline_percentage = float(
            baseline["summary"].get("coverage_percentage", float("nan"))
        )
        result["comparison"] = {
            "baseline_result": str(baseline_path),
            "baseline_coverage_percentage": baseline_percentage,
            "current_coverage_percentage": coverage_percentage,
            "coverage_percentage_change": coverage_percentage
            - baseline_percentage,
            "improved": coverage_percentage > baseline_percentage,
        }

    (output_dir / "summary.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "summary.md").write_text(report_markdown(result), encoding="utf-8")
    return output_dir, result


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Evaluate live-query coverage with the production embedding provider"
    )
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--name", default="run")
    parser.add_argument("--ollama-url", default=settings.ollama_url)
    parser.add_argument("--embedding-model", default=settings.embed_model)
    parser.add_argument(
        "--similarity-threshold",
        type=float,
        default=DEFAULT_SIMILARITY_THRESHOLD,
    )
    parser.add_argument(
        "--minimum-coverage",
        type=float,
        default=DEFAULT_MINIMUM_COVERAGE,
    )
    parser.add_argument("--baseline-result", type=Path)
    args = parser.parse_args()
    try:
        output_dir, result = run(
            queries_path=args.queries,
            golden_path=args.golden,
            output_root=args.out_root,
            name=args.name,
            ollama_url=args.ollama_url,
            model=args.embedding_model,
            similarity_threshold=args.similarity_threshold,
            minimum_coverage=args.minimum_coverage,
            baseline_path=args.baseline_result,
        )
    except (
        CoverageEvaluationError,
        golden_schema.GoldenSchemaError,
        ModuleEmbeddingError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2
    summary = result["summary"]
    print(
        json.dumps(
            {
                "status": "ok" if summary["gate_passed"] else "coverage-gap",
                "output_dir": str(output_dir),
                "coverage_percentage": summary["coverage_percentage"],
                "covered_queries": summary["covered_queries"],
                "uncovered_queries": summary["uncovered_queries"],
                "similarity_threshold": args.similarity_threshold,
                "minimum_coverage_percentage": args.minimum_coverage * 100.0,
            },
            indent=2,
        )
    )
    return gate_exit_code(
        summary["coverage_percentage"], args.minimum_coverage
    )


if __name__ == "__main__":
    raise SystemExit(main())
