"""Retrieve relevant evidence, generate a validated answer, and build the API response."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from pydantic import ValidationError

from ..config import Settings
from ..guardrails import inspect_untrusted_content
from ..logjson import request_id_var
from ..metrics import ANSWERS, INJECTION_ATTEMPTS, STAGE_LATENCY, TOP1_SIMILARITY
from ..providers.llm.base import InvalidAnswerError, LLMProvider
from ..retrieval.base import Retriever
from ..schemas import Answer, AskResponse, Citation, Passage, RetrievedRef
from ..tracing import write_trace
from .semantic_cache import CACHE_LOOKUPS, CacheLookup, SemanticCache

log = logging.getLogger("rahbar.answer")

_ABSTENTION_TEXT = "The available documents do not provide relevant information to answer this question."
_FALLBACK_TEXT = (
    "I couldn't produce a reliable answer from the available documents. "
    "Please try again later."
)


def _trace_latency_float(metadata: dict[str, object], key: str) -> float:
    value = metadata.get(key, 0.0)
    if isinstance(value, (str, int, float)):
        try:
            return float(value)
        except (OverflowError, ValueError):
            pass
    return 0.0


def _abstention() -> Answer:
    return Answer(
        reasoning="No retrieved passage met the configured relevance threshold.",
        answer=_ABSTENTION_TEXT,
        citations=[],
        answered_from_context=False,
        confidence="low",
    )


def _fallback() -> Answer:
    return Answer(
        reasoning="Answer generation or validation failed.",
        answer=_FALLBACK_TEXT,
        citations=[],
        answered_from_context=False,
        confidence="low",
    )


_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "by", "for", "from", "how",
    "in", "is", "it", "of", "on", "the", "to", "what", "when", "which",
    "with", "must", "should", "can", "does",
    # Pronouns and auxiliaries carry no evidence; counting them under-scores
    # natural questions such as "If I repeat a course, is my grade capped?".
    "if", "do", "be", "was", "will", "this", "that",
    "my", "me", "we", "our", "you", "your",
}


def _words(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z0-9]+", text.casefold())
        if word not in _STOP_WORDS and len(word) > 1
    }


def _support_score(answer_text: str, passage: Passage) -> float:
    answer_words = _words(answer_text)
    passage_words = _words(passage.text)
    if not answer_words:
        return 0.0
    overlap = len(answer_words & passage_words)
    answer_coverage = overlap / len(answer_words)
    normalized_answer = re.sub(r"\s+", " ", answer_text.casefold()).strip()
    normalized_passage = re.sub(r"\s+", " ", passage.text.casefold())
    exact_bonus = 0.35 if len(normalized_answer) >= 8 and normalized_answer in normalized_passage else 0.0
    numeric_answer = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", answer_text))
    numeric_overlap = sum(number in passage.text for number in numeric_answer)
    numeric_bonus = 0.35 if numeric_answer and numeric_overlap == len(numeric_answer) else 0.0
    return answer_coverage * 0.65 + exact_bonus + numeric_bonus


def _question_support_score(question: str, passages: list[Passage]) -> float:
    """Measure whether retrieved text covers the substantive wording of a question."""
    question_words = _words(question)
    passage_words: set[str] = set()
    for passage in passages:
        passage_words |= _words(passage.text)
        passage_words |= _words(passage.source)
        passage_words |= _words(passage.section)
        if passage.doc_version:
            passage_words |= _words(passage.doc_version)
    if not question_words:
        return 0.0
    overlap = len(question_words & passage_words)
    coverage = overlap / len(question_words)

    # A question with several requested facts must have evidence for every clause.
    clauses = [
        clause.strip()
        for clause in re.split(r"\b(?:and|or)\b|[;,]", question.casefold())
        if clause.strip()
    ]
    clause_support: list[float] = []
    for clause in clauses:
        clause_words = _words(clause)
        clause_overlap = len(clause_words & passage_words)
        clause_support.append(clause_overlap / len(clause_words) if clause_words else 0.0)

    if clause_support and len(clause_support) > 1:
        weakest_clause = min(clause_support)
        if weakest_clause < 0.40:
            return min(coverage, weakest_clause)
    return coverage


def _has_sufficient_evidence(question: str, passages: list[Passage]) -> bool:
    if not passages:
        return False
    question_words = _words(question)
    if len(question_words) <= 1:
        return True

    passage_words: set[str] = set()
    for passage in passages:
        passage_words |= _words(passage.text)
        passage_words |= _words(passage.source)
        passage_words |= _words(passage.section)
        if passage.doc_version:
            passage_words |= _words(passage.doc_version)

    clauses = [
        clause.strip()
        for clause in re.split(r"\b(?:and|or)\b|[;,]", question.casefold())
        if clause.strip()
    ]
    if len(clauses) > 1:
        clause_support: list[float] = []
        for clause in clauses:
            clause_words = _words(clause)
            if not clause_words:
                continue
            clause_support.append(
                len(clause_words & passage_words) / len(clause_words)
            )
        if clause_support and min(clause_support) < 0.35:
            return False
        return True

    support = _question_support_score(question, passages)
    return support >= 0.10


def _citation_matches_passage(citation: object, passage: Passage) -> bool:
    source = getattr(citation, "source", None)
    section = getattr(citation, "section", None)
    page = getattr(citation, "page", None)
    return (
        source == passage.source
        and page == passage.page
        and (
            not section
            or not passage.section
            or " ".join(section.split()).casefold()
            == " ".join(passage.section.split()).casefold()
        )
    )


def _reconcile_answer_state(
    question: str,
    answer: Answer,
    passages: list[Passage],
) -> Answer:
    """Keep grounded answers and citations aligned without overriding genuine abstention."""
    # Reconcile only against the question passed to this call. The retriever cannot prove
    # factual support, and a weak vector score may admit a merely related passage.
    if not passages:
        return answer.model_copy(update={
            "answered_from_context": False,
            "citations": [],
            "confidence": "low",
        })
    if not _has_sufficient_evidence(question, passages):
        return _abstention()
    normalized_answer = re.sub(r"\s+", " ", answer.answer.casefold()).strip()
    abstention_markers = (
        "do not provide relevant information",
        "documents do not",
        "cannot be answered",
        "cannot answer",
        "available documents",
        "not available",
        "not enough information",
        "insufficient information",
        "evidence does not cover",
        "i don't have",
        "i do not have",
        "i can only respond based on",
        "couldn't produce",
    )
    is_abstention = any(marker in normalized_answer for marker in abstention_markers)
    if is_abstention:
        return answer.model_copy(
            update={
                "answered_from_context": False,
                "citations": [],
                "confidence": "low",
            }
        )
    ranked = sorted(
        (
            (_support_score(answer.answer, passage), passage)
            for passage in passages
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    supporting = [
        passage
        for score, passage in ranked
        if score >= 0.25
    ]
    if not supporting:
        supporting = []

    cited_supporting: list[object] = []
    for citation in answer.citations:
        matches = [
            passage
            for passage in supporting
            if _citation_matches_passage(citation, passage)
        ]
        if matches:
            cited_supporting.append(citation)

    if answer.answered_from_context and cited_supporting:
        selected_citations = cited_supporting
    elif answer.answered_from_context and not cited_supporting:
        selected_citations = [
            Citation(
                source=passage.source,
                section=passage.section,
                page=passage.page,
            )
            for passage in supporting[:2]
        ]
    elif not answer.answered_from_context and not is_abstention:
        selected_citations = [
            Citation(
                source=passage.source,
                section=passage.section,
                page=passage.page,
            )
            for passage in supporting[:2]
        ]
    else:
        selected_citations = []

    grounded = bool(selected_citations)
    if grounded:
        return answer.model_copy(update={
            "answered_from_context": True,
            "citations": selected_citations,
            "confidence": "high",
        })
    return _abstention()


def _validate_citations(answer: Answer, passages: list[Passage]) -> None:
    available = {(passage.source, passage.section, passage.page) for passage in passages}
    for citation in answer.citations:
        if (citation.source, citation.section, citation.page) not in available:
            raise InvalidAnswerError(
                "Generated citation does not match retrieved evidence"
            )


def _validated_generation(
    question: str, passages: list[Passage], provider: LLMProvider
) -> Answer:
    for attempt in range(2):
        try:
            candidate = provider.generate(question, passages)
            answer = Answer.model_validate(candidate)
            _validate_citations(answer, passages)
            return _reconcile_answer_state(question, answer, passages)
        except (InvalidAnswerError, ValidationError, ValueError, TypeError) as exc:
            if attempt == 0:
                log.warning(
                    "invalid model answer; retrying",
                    extra={"provider": provider.name, "error": str(exc)},
                )
                continue
            log.error(
                "model answer remained invalid after retry",
                extra={"provider": provider.name, "error": str(exc)},
            )
            return _fallback()
        except Exception as exc:
            log.error(
                "answer generation failed",
                extra={"provider": provider.name, "error": str(exc)},
            )
            return _fallback()
    return _fallback()


def _trace_passage(passage: Passage, rank: int) -> dict[str, Any]:
    if passage.document_id:
        document_id = passage.document_id
        document_id_source = "chroma_metadata"
    elif passage.source and passage.doc_version:
        document_id = f"{passage.source}@{passage.doc_version}"
        document_id_source = "derived_from_source_and_version"
    else:
        document_id = None
        document_id_source = "not_recorded"
    return {
        "rank": rank,
        "chunk_id": passage.chunk_id,
        "document_id": document_id,
        "document_id_source": document_id_source,
        "source": passage.source,
        "document_version": passage.doc_version,
        "page": passage.page,
        "section": passage.section,
        "score": passage.score,
        "text": passage.text,
    }


def _prompt_payload(provider: LLMProvider, question: str, passages: list[Passage]) -> object:
    trace_payload = getattr(provider, "trace_payload", None)
    if not callable(trace_payload):
        return {"status": "unavailable", "reason": "provider does not expose its payload"}
    payload = trace_payload(question, passages)
    json.dumps(payload, ensure_ascii=False, allow_nan=False)
    return payload if isinstance(payload, dict) and isinstance(payload.get("messages"), list) else None


def _write_query_trace(
    question: str,
    response: AskResponse,
    settings: Settings,
    *,
    passages: list[Passage],
    evidence: list[Passage],
    retrieval_metadata: dict[str, object],
    guardrail: dict[str, object],
    cache_status: str,
    cached_from_trace_id: str | None,
    prompt_payload: object,
    generation_model: str,
    stage_latency_ms: dict[str, float],
) -> None:
    prompt_hash = hashlib.sha256(
        json.dumps(
            prompt_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest() if prompt_payload is not None else None
    timestamp = datetime.now(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    top1_similarity = response.retrieved[0].score if response.retrieved else None
    trace = {
        "trace_record_version": 1,
        "trace_id": response.request_id,
        "query": question,
        "timestamp": timestamp,
        "original_query": question,
        "rewritten_query": None,
        "rewrite_status": "not_configured",
        "embedding_model": {
            "name": settings.embed_model,
            "version": settings.embed_model_version or settings.embed_model,
        },
        "collection": {
            "name": settings.collection,
            "version": retrieval_metadata.get("collection_version"),
            "version_source": retrieval_metadata.get("collection_version_source"),
            "id": retrieval_metadata.get("collection_id"),
            "metadata": retrieval_metadata.get("collection_metadata", {}),
        },
        "collection_name": settings.collection,
        "embedding_model_version": settings.embed_model,
        "retrieved": [
            _trace_passage(passage, rank)
            for rank, passage in enumerate(passages, start=1)
        ],
        "retrieval": {
            "filters_applied": retrieval_metadata.get("filters_applied", []),
            "candidate_count": retrieval_metadata.get("candidate_count"),
            "raw_ranked_order": retrieval_metadata.get(
                "raw_ranked_order", [passage.chunk_id for passage in passages]
            ),
            "reranked_order": retrieval_metadata.get(
                "reranked_order", [passage.chunk_id for passage in passages]
            ),
            "top1_similarity": top1_similarity,
        },
        "context": [_trace_passage(passage, rank) for rank, passage in enumerate(evidence, 1)],
        "prompt_payload": prompt_payload,
        "prompt_hash": prompt_hash,
        "generation": {
            "provider": response.provider,
            "model": {
                "name": generation_model,
                "version": settings.llm_model_version or generation_model,
            },
            "parameters": {
                "temperature": settings.temperature,
            },
            "sent": (
                isinstance(prompt_payload, dict)
                and isinstance(prompt_payload.get("messages"), list)
            ),
        },
        "generation_model": generation_model,
        "provider": response.provider,
        "response": {
            "answer": response.answer,
            "citations": [citation.model_dump(mode="json") for citation in response.citations],
            "answered_from_context": response.answered_from_context,
            "confidence": response.confidence,
        },
        "answer": response.answer,
        "answered_from_context": response.answered_from_context,
        "retrieved_chunk_ids": [item.chunk_id for item in response.retrieved],
        "citations": [citation.model_dump(mode="json") for citation in response.citations],
        "guardrail": guardrail,
        "stage_latency_ms": stage_latency_ms,
        "cache": {
            "status": cache_status,
            "hit": cache_status == "hit",
            "source_trace_id": cached_from_trace_id,
        },
        "cache_hit": cache_status == "hit",
    }
    write_trace(trace, settings.trace_file)
    ANSWERS.labels(
        outcome="answered" if response.answered_from_context else "abstained"
    ).inc()
    if top1_similarity is not None:
        TOP1_SIMILARITY.observe(top1_similarity)
    for stage, duration_ms in stage_latency_ms.items():
        STAGE_LATENCY.labels(stage=stage).observe(duration_ms / 1000)
    if guardrail.get("detected"):
        INJECTION_ATTEMPTS.inc()


def answer_question(
    question: str,
    retriever: Retriever,
    provider: LLMProvider,
    settings: Settings,
    cache: SemanticCache | None = None,
) -> AskResponse:
    if not 0.0 <= settings.relevance_floor <= 1.0 or not math.isfinite(
        settings.relevance_floor
    ):
        raise ValueError("relevance_floor must be between 0.0 and 1.0")

    total_started = perf_counter()
    guardrail_started = perf_counter()
    _, query_guardrail = inspect_untrusted_content(question, [])
    guardrail_latency_ms = (perf_counter() - guardrail_started) * 1000
    retrieval_metadata: dict[str, object] = {}
    passages: list[Passage] = []
    evidence: list[Passage] = []
    prompt_payload: object = None
    cached_from_trace_id: str | None = None
    cache_started = perf_counter()
    if query_guardrail.get("detected"):
        CACHE_LOOKUPS.labels(result="bypass").inc()
        cache_lookup = CacheLookup(
            embedding=None,
            response=None,
            may_store=False,
            generation=None,
            status="bypassed_guardrail",
        )
        cache_status = "bypassed_guardrail"
    else:
        cache_lookup = (
            cache.lookup(question)
            if cache is not None
            else CacheLookup(embedding=None, response=None, may_store=False, generation=None)
        )
        cache_status = cache_lookup.status
        if cache_status == "unknown":
            cache_status = "disabled" if cache is None else (
                "hit" if cache_lookup.response is not None
                else "miss" if cache_lookup.may_store
                else "bypassed_or_error"
            )
        if cache is None:
            CACHE_LOOKUPS.labels(result="disabled").inc()
    cache_latency_ms = (perf_counter() - cache_started) * 1000
    cache_embedding_ms = cache_lookup.embedding_latency_ms
    cache_lookup_ms = max(0.0, cache_latency_ms - cache_embedding_ms)

    if cache_lookup.response is not None:
        log.info("answer served from semantic cache")
        cached_from_trace_id = cache_lookup.response.request_id
        response = cache_lookup.response.model_copy(
            update={"request_id": request_id_var.get()}
        )
        stage_latency_ms = {
            "cache_lookup": cache_lookup_ms,
            "embedding": cache_embedding_ms,
            "guardrail": guardrail_latency_ms,
            "total": (perf_counter() - total_started) * 1000,
        }
        _write_query_trace(
            question,
            response,
            settings,
            passages=[],
            evidence=[],
            retrieval_metadata={},
            guardrail=query_guardrail,
            cache_status=cache_status,
            cached_from_trace_id=cached_from_trace_id,
            prompt_payload=None,
            generation_model=settings.llm_model,
            stage_latency_ms=stage_latency_ms,
        )
        return response

    if query_guardrail.get("detected"):
        guardrail = query_guardrail
        answer = _abstention()
    else:
        retrieval_started = perf_counter()
        passages = retriever.retrieve(
            question,
            k=settings.top_k,
            query_embedding=cache_lookup.embedding,
        )
        retrieval_latency_ms = (perf_counter() - retrieval_started) * 1000
        retrieval_metadata = dict(getattr(passages, "trace_metadata", {}))
        guardrail_started = perf_counter()
        safe_passages, guardrail = inspect_untrusted_content(question, passages)
        guardrail_latency_ms += (perf_counter() - guardrail_started) * 1000
        evidence = [
            passage
            for passage in safe_passages
            if math.isfinite(passage.score) and passage.score >= settings.relevance_floor
        ]
        generation_started = perf_counter()
        if evidence and _has_sufficient_evidence(question, evidence):
            try:
                prompt_payload = _prompt_payload(provider, question, evidence)
            except (ValueError, RuntimeError, TypeError) as exc:
                prompt_payload = {
                    "status": "capture_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                log.error("could not capture provider prompt payload", extra={"error": str(exc)})
            answer = _validated_generation(question, evidence, provider)
        else:
            answer = _abstention()
        generation_latency_ms = (perf_counter() - generation_started) * 1000
        retrieval_metadata["retrieval_latency_ms"] = retrieval_latency_ms
        retrieval_metadata["generation_latency_ms"] = generation_latency_ms

    log.info(
        "answered",
        extra={
            "retriever": retriever.name,
            "provider": provider.name,
            "top_k": settings.top_k,
            "chunk_ids": [p.chunk_id for p in passages],
            "scores": [round(p.score, 4) for p in passages],
            "answered_from_context": answer.answered_from_context,
        },
    )
    response = AskResponse(
        request_id=request_id_var.get(),
        answer=answer.answer,
        citations=answer.citations,
        answered_from_context=answer.answered_from_context,
        confidence=answer.confidence,
        retrieved=[
            RetrievedRef(
                chunk_id=p.chunk_id, source=p.source, section=p.section, page=p.page, score=p.score
            )
            for p in passages
        ],
        provider=provider.name,
    )
    stage_latency_ms = {
        "cache_lookup": cache_lookup_ms,
        "embedding": cache_embedding_ms
        + _trace_latency_float(retrieval_metadata, "embedding_latency_ms"),
        "vector_query": _trace_latency_float(
            retrieval_metadata, "vector_query_latency_ms"
        ),
        "reranking": _trace_latency_float(retrieval_metadata, "reranking_latency_ms"),
        "retrieval": _trace_latency_float(retrieval_metadata, "retrieval_latency_ms"),
        "generation": _trace_latency_float(
            retrieval_metadata, "generation_latency_ms"
        ),
        "guardrail": guardrail_latency_ms,
        "total": (perf_counter() - total_started) * 1000,
    }
    _write_query_trace(
        question,
        response,
        settings,
        passages=passages,
        evidence=evidence,
        retrieval_metadata=retrieval_metadata,
        guardrail=guardrail,
        cache_status=cache_status,
        cached_from_trace_id=cached_from_trace_id,
        prompt_payload=prompt_payload,
        generation_model=settings.llm_model,
        stage_latency_ms=stage_latency_ms,
    )
    if cache is not None:
        cache.store(
            cache_lookup.embedding,
            response,
            may_store=cache_lookup.may_store,
            generation=cache_lookup.generation,
        )
    return response
