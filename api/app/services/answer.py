"""Retrieve relevant evidence, generate a validated answer, and build the API response."""

from __future__ import annotations

import logging
import math

from pydantic import ValidationError

from ..config import Settings
from ..logjson import request_id_var
from ..providers.llm.base import InvalidAnswerError, LLMProvider
from ..retrieval.base import Retriever
from ..schemas import Answer, AskResponse, Passage, RetrievedRef
from .semantic_cache import CACHE_LOOKUPS, CacheLookup, SemanticCache

log = logging.getLogger("rahbar.answer")

_ABSTENTION_TEXT = "The available documents do not provide relevant information to answer this question."
_FALLBACK_TEXT = (
    "I couldn't produce a reliable answer from the available documents. "
    "Please try again later."
)


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
            return answer
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

    cache_lookup = (
        cache.lookup(question)
        if cache is not None
        else CacheLookup(embedding=None, response=None, may_store=False, generation=None)
    )
    if cache_lookup.response is not None:
        log.info("answer served from semantic cache")
        return cache_lookup.response.model_copy(
            update={"request_id": request_id_var.get()}
        )

    if cache is None:
        CACHE_LOOKUPS.labels(result="disabled").inc()

    passages = retriever.retrieve(
        question,
        k=settings.top_k,
        query_embedding=cache_lookup.embedding,
    )
    evidence = [
        passage
        for passage in passages
        if math.isfinite(passage.score) and passage.score >= settings.relevance_floor
    ]
    answer = _validated_generation(question, evidence, provider) if evidence else _abstention()

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
    if cache is not None:
        cache.store(
            cache_lookup.embedding,
            response,
            may_store=cache_lookup.may_store,
            generation=cache_lookup.generation,
        )
    return response
