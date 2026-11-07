from __future__ import annotations

import fnmatch
import json
import math
from pathlib import Path
from typing import Any

import httpx
import pytest

from eval import run_cache_threshold_eval as threshold_eval

from app.config import Settings
from app.embeddings import embed_query as production_embed_query
from app.schemas import AskResponse, Citation, RetrievedRef
from app.services import semantic_cache as production_cache


def _experiment(
    pairs: tuple[threshold_eval.QueryPair, ...],
    thresholds: tuple[float, ...] = (0.90, 0.95, 0.97),
) -> threshold_eval.Experiment:
    return threshold_eval.Experiment(thresholds, pairs)


def _valid_cached_response() -> AskResponse:
    return AskResponse(
        request_id="stored-request",
        answer="Students need 80% attendance.",
        citations=[Citation(source="rules.pdf", section="Attendance", page=2)],
        answered_from_context=True,
        confidence="high",
        retrieved=[
            RetrievedRef(
                chunk_id="chunk-1",
                source="rules.pdf",
                section="Attendance",
                page=2,
                score=0.9,
            )
        ],
        provider="test",
    )


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def scan_iter(self, *, match: str):
        return (
            key for key in self.values if fnmatch.fnmatch(key, match)
        )

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str, *, ex: int) -> bool:
        assert ex > 0
        self.values[key] = value
        return True

    def incr(self, key: str) -> int:
        generation = int(self.values.get(key, "0")) + 1
        self.values[key] = str(generation)
        return generation

    def close(self) -> None:
        pass


def test_pair_dataset_has_explicit_labels_and_required_categories() -> None:
    experiment = threshold_eval.load_experiment()

    assert len(experiment.thresholds) >= 3
    assert threshold_eval.PRODUCTION_THRESHOLD in experiment.thresholds
    assert len(experiment.pairs) >= 15
    assert all(isinstance(pair.expected_reuse, bool) for pair in experiment.pairs)
    assert {pair.category for pair in experiment.pairs} >= {
        "equivalent",
        "paraphrase",
        "hard_negative",
        "version_sensitive",
        "entity_sensitive",
        "unrelated",
    }
    assert all(
        pair.expected_reuse is False
        for pair in experiment.pairs
        if pair.category
        in {"hard_negative", "version_sensitive", "entity_sensitive", "unrelated"}
    )


@pytest.mark.parametrize(
    ("similarity", "threshold", "expected"),
    [
        (0.95, 0.95, True),
        (math.nextafter(0.95, 0.0), 0.95, False),
        (math.nextafter(0.95, 1.0), 0.95, True),
    ],
)
def test_threshold_uses_production_inclusive_boundary(
    similarity: float,
    threshold: float,
    expected: bool,
) -> None:
    assert threshold_eval.threshold_decision(similarity, threshold) is expected


def test_equivalent_and_hard_negative_pairs_are_scored_per_threshold() -> None:
    equivalent = threshold_eval.QueryPair(
        id="equivalent",
        category="equivalent",
        expected_reuse=True,
        cached_question="Same meaning A",
        query_question="Same meaning B",
        rationale="Same grounded answer.",
    )
    hard_negative = threshold_eval.QueryPair(
        id="hard-negative",
        category="hard_negative",
        expected_reuse=False,
        cached_question="Attendance requirement?",
        query_question="Attendance consequence?",
        rationale="Related topic, distinct answer.",
    )
    rows, summaries = threshold_eval.evaluate_vectors(
        _experiment((equivalent, hard_negative), (0.90, 0.95, 0.99)),
        {
            "Same meaning A": [1.0, 0.0],
            "Same meaning B": [0.98, math.sqrt(1 - 0.98**2)],
            "Attendance requirement?": [1.0, 0.0],
            "Attendance consequence?": [0.92, math.sqrt(1 - 0.92**2)],
        },
    )

    assert [row["cosine_similarity"] for row in rows] == [0.98, 0.92]
    assert summaries[0]["true_hits"] == 1
    assert summaries[0]["false_hits"] == 1
    assert summaries[0]["missed_hits"] == 0
    assert summaries[1]["false_hits"] == 0
    assert summaries[2]["false_hits"] == 0
    assert summaries[2]["missed_hits"] == 1


def test_metrics_define_hit_rate_precision_and_false_hit_rate() -> None:
    summary = threshold_eval.summarize_threshold(
        0.95,
        [
            {"expected_reuse": True, "actual_reuse": True},
            {"expected_reuse": True, "actual_reuse": False},
            {"expected_reuse": False, "actual_reuse": True},
            {"expected_reuse": False, "actual_reuse": False},
        ],
    )

    assert summary["true_hits"] == 1
    assert summary["false_hits"] == 1
    assert summary["missed_hits"] == 1
    assert summary["hit_rate_percentage"] == 50.0
    assert summary["precision_percentage"] == 50.0
    assert summary["positive_pair_recall_percentage"] == 50.0
    assert summary["false_hit_rate_percentage"] == 50.0


def test_positive_pair_recall_is_unavailable_without_positive_pairs() -> None:
    summary = threshold_eval.summarize_threshold(
        0.95,
        [
            {"expected_reuse": False, "actual_reuse": True},
            {"expected_reuse": False, "actual_reuse": False},
        ],
    )

    assert summary["positive_pair_count"] == 0
    assert summary["true_hits"] == 0
    assert summary["positive_pair_recall_percentage"] is None


@pytest.mark.parametrize(
    ("true_hits", "positive_pair_count", "expected_recall"),
    [(5, 8, 62.5), (3, 8, 37.5), (2, 8, 25.0), (1, 8, 12.5)],
)
def test_positive_pair_recall_matches_recorded_run_values(
    true_hits: int,
    positive_pair_count: int,
    expected_recall: float,
) -> None:
    rows = [
        {"expected_reuse": True, "actual_reuse": index < true_hits}
        for index in range(positive_pair_count)
    ]
    rows.extend(
        {"expected_reuse": False, "actual_reuse": False}
        for _ in range(12)
    )

    summary = threshold_eval.summarize_threshold(0.95, rows)

    assert summary["positive_pair_recall_percentage"] == expected_recall


def test_dataset_rejects_missing_or_inferred_expected_outcomes(tmp_path: Path) -> None:
    path = tmp_path / "pairs.yaml"
    path.write_text(
        """
schema_version: 1
thresholds: [0.90, 0.95, 0.97]
pairs:
  - id: missing-label
    category: hard_negative
    cached_question: One question?
    query_question: Another question?
    rationale: Distinct meanings.
""",
        encoding="utf-8",
    )

    with pytest.raises(threshold_eval.CacheThresholdError, match="expected_reuse"):
        threshold_eval.load_experiment(path)


def test_cache_collection_scope_and_generation_isolate_pair_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(
        production_cache,
        "embed_query",
        lambda _question, _settings: [1.0, 0.0],
    )
    first_settings = Settings(
        redis_url="redis://unused",
        cache_prefix="rahbar:semantic-cache",
        collection="collection-one",
        cache_similarity_threshold=0.95,
    )
    other_collection_settings = Settings(
        redis_url="redis://unused",
        cache_prefix="rahbar:semantic-cache",
        collection="collection-two",
        cache_similarity_threshold=0.95,
    )
    first_cache = production_cache.SemanticCache(redis, first_settings)
    other_collection_cache = production_cache.SemanticCache(redis, other_collection_settings)

    old_lookup = first_cache.lookup("What attendance level is required?")
    first_cache.store(
        old_lookup.embedding,
        _valid_cached_response(),
        may_store=old_lookup.may_store,
        generation=old_lookup.generation,
    )
    assert other_collection_cache.lookup("What attendance level is required?").response is None

    redis.incr(first_cache.generation_key)
    first_cache.store(
        old_lookup.embedding,
        _valid_cached_response(),
        may_store=old_lookup.may_store,
        generation=old_lookup.generation,
    )
    refreshed_lookup = first_cache.lookup("What attendance level is required?")

    assert old_lookup.generation == "0"
    assert refreshed_lookup.generation == "1"
    assert refreshed_lookup.response is None
    assert first_cache.key_prefix != other_collection_cache.key_prefix
    assert any(":entry:0:" in key for key in redis.values)


def test_embedding_helper_uses_production_endpoint_and_accepts_embedding_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any], float]] = []

    def post(url: str, *, json: dict[str, Any], timeout: float) -> httpx.Response:
        calls.append((url, json, timeout))
        return httpx.Response(
            200,
            json={"embeddings": [[1.0, 2.0]]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.embeddings.httpx.post", post)
    settings = Settings(embed_model="nomic-embed-text", ollama_url="http://ollama.local")

    assert production_embed_query("Question?", settings) == [1.0, 2.0]
    assert calls == [
        (
            "http://ollama.local/api/embed",
            {"model": "nomic-embed-text", "input": "Question?"},
            120.0,
        )
    ]


@pytest.mark.parametrize(
    "payload",
    [
        '{"embedding":[1.0,2.0]}',
        '{"embeddings":[]}',
        '{"embeddings":[[1.0,NaN]]}',
        '{"embeddings":[[1.0,true]]}',
    ],
)
def test_embedding_helper_rejects_malformed_responses(
    monkeypatch: pytest.MonkeyPatch,
    payload: str,
) -> None:
    monkeypatch.setattr(
        "app.embeddings.httpx.post",
        lambda url, **_kwargs: httpx.Response(
            200, content=payload.encode(), request=httpx.Request("POST", url)
        ),
    )

    with pytest.raises(RuntimeError):
        production_embed_query("Question?", Settings())


def test_unavailable_embedding_service_fails_the_experiment_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair = threshold_eval.QueryPair(
        id="offline",
        category="equivalent",
        expected_reuse=True,
        cached_question="A question?",
        query_question="A paraphrase?",
        rationale="Same meaning.",
    )
    monkeypatch.setattr(
        threshold_eval,
        "embed_query",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("service unavailable")),
    )

    with pytest.raises(threshold_eval.CacheThresholdError, match="embedding request failed"):
        threshold_eval._embed_questions(
            _experiment((pair,)),
            ollama_url="http://ollama.local",
            model="nomic-embed-text",
        )


def test_model_identity_records_resolved_tag_and_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        threshold_eval.httpx,
        "get",
        lambda url, *, timeout: httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "nomic-embed-text:latest",
                        "digest": "model-digest",
                    }
                ]
            },
            request=httpx.Request("GET", url),
        ),
    )

    assert threshold_eval._resolve_model_identity(
        "http://ollama.local", "nomic-embed-text"
    ) == {
        "configured": "nomic-embed-text",
        "name": "nomic-embed-text:latest",
        "digest": "model-digest",
    }


def test_output_serialization_and_collision_protection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        threshold_eval,
        "_embed_questions",
        lambda *_args, **_kwargs: {
            "A question?": [1.0, 0.0],
            "A paraphrase?": [1.0, 0.0],
            "Related question one?": [1.0, 0.0],
            "Related question two?": [0.0, 1.0],
        },
    )
    monkeypatch.setattr(
        threshold_eval,
        "_resolve_model_identity",
        lambda *_args: {
            "configured": "nomic-embed-text",
            "name": "nomic-embed-text:latest",
            "digest": "test-digest",
        },
    )
    pair_path = tmp_path / "pairs.yaml"
    pair_path.write_text(
        """
schema_version: 1
thresholds: [0.90, 0.95, 0.97]
pairs:
  - id: pair-1
    category: equivalent
    expected_reuse: true
    cached_question: A question?
    query_question: A paraphrase?
    rationale: Same meaning.
  - id: hard-negative
    category: hard_negative
    expected_reuse: false
    cached_question: Related question one?
    query_question: Related question two?
    rationale: Different answer.
""",
        encoding="utf-8",
    )
    output = tmp_path / "run"
    threshold_eval.run_evaluation(
        output_dir=output,
        pairs_path=pair_path,
        ollama_url="http://ollama.local",
        model="nomic-embed-text",
    )

    results = json.loads((output / "pair-results.json").read_text(encoding="utf-8"))
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert results["pairs"][0]["threshold_decisions"][0]["actual_reuse"] is True
    assert len(summary["metadata"]["runner_sha256"]) == 64
    assert summary["metadata"]["unique_question_count"] == 4
    assert summary["lowest_tested_zero_false_hit_threshold"] == 0.9
    assert summary["metadata"]["isolation_assumptions"]["redis_operations_and_invalidation_are_not_executed"]
    with pytest.raises(threshold_eval.CacheThresholdError, match="refusing to overwrite"):
        threshold_eval.run_evaluation(
            output_dir=output,
            pairs_path=pair_path,
            ollama_url="http://ollama.local",
            model="nomic-embed-text",
        )
