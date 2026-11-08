"""Liveness and readiness (section 4.3). They answer different questions."""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Request, Response
import httpx
from redis import Redis

from ..config import Settings
from ..embeddings import embedding_dimension

router = APIRouter()
log = logging.getLogger("rahbar.api.health")

_CHROMA_TIMEOUT_SECONDS = 2.0
_REDIS_TIMEOUT_SECONDS = 1.0
_CHROMA_TENANT = "default_tenant"
_CHROMA_DATABASE = "default_database"


def _check_chroma(settings: Settings) -> tuple[dict[str, str], int | None]:
    host = settings.chroma_host.rstrip("/")
    if host.startswith(("http://", "https://")):
        base_url = host
    else:
        base_url = f"http://{host}:{settings.chroma_port}"
    if not base_url.endswith("/api/v2"):
        base_url = f"{base_url}/api/v2"

    collection_url = (
        f"{base_url}/tenants/{_CHROMA_TENANT}/databases/{_CHROMA_DATABASE}"
        f"/collections/{quote(settings.collection, safe='')}"
    )
    with httpx.Client(timeout=httpx.Timeout(_CHROMA_TIMEOUT_SECONDS)) as client:
        collection_response = client.get(collection_url)
        collection_response.raise_for_status()
        collection = collection_response.json()

        if not isinstance(collection, dict):
            return (
                {
                    "status": "unavailable",
                    "detail": "Chroma returned invalid collection information.",
                },
                None,
            )
        metadata = collection.get("metadata")
        if not isinstance(metadata, dict):
            return (
                {
                    "status": "unavailable",
                    "detail": "The configured Chroma collection has no embedding metadata.",
                },
                None,
            )
        if metadata.get("embed_model") != settings.embed_model:
            return (
                {
                    "status": "incompatible",
                    "detail": "The collection embedding model does not match EMBED_MODEL.",
                },
                None,
            )
        dimension = metadata.get("dimension")
        if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0:
            return (
                {
                    "status": "incompatible",
                    "detail": "The collection embedding dimension metadata is missing or invalid.",
                },
                None,
            )
        if metadata.get("hnsw:space") != "cosine":
            return (
                {
                    "status": "incompatible",
                    "detail": "The collection distance metadata is incompatible with cosine retrieval.",
                },
                None,
            )

        collection_id = collection.get("id")
        if not isinstance(collection_id, str) or not collection_id:
            return (
                {
                    "status": "unavailable",
                    "detail": "Chroma returned invalid collection information.",
                },
                None,
            )
        count_url = (
            f"{base_url}/tenants/{_CHROMA_TENANT}/databases/{_CHROMA_DATABASE}"
            f"/collections/{quote(collection_id, safe='')}/count"
        )
        count_response = client.get(count_url)
        count_response.raise_for_status()
        count = count_response.json()

    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return (
            {
                "status": "unavailable",
                "detail": "Chroma returned an invalid collection record count.",
            },
            None,
        )
    if count == 0:
        return (
            {
                "status": "unavailable",
                "detail": "The configured Chroma collection has no indexed records.",
            },
            None,
        )
    return {"status": "ok"}, dimension


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Is the process alive? Must never touch Chroma, Redis or the LLM."""
    return {"status": "alive"}


@router.get("/readyz")
def readyz(request: Request, response: Response) -> dict[str, object]:
    settings = request.app.state.settings
    checks: dict[str, dict[str, str]] = {}
    ready = True

    if settings.retriever == "chroma":
        try:
            chroma_check, stored_dimension = _check_chroma(settings)
        except Exception as exc:
            log.warning(
                "readiness check failed",
                extra={"dependency": "chroma", "error_type": type(exc).__name__},
            )
            chroma_check = {
                "status": "unavailable",
                "detail": "Chroma is unreachable or the configured collection is unavailable.",
            }
            stored_dimension = None
        checks["chroma"] = chroma_check
        if chroma_check["status"] != "ok":
            ready = False
        elif stored_dimension is not None:
            try:
                configured_dimension = embedding_dimension(
                    settings.embed_model,
                    settings.ollama_url,
                )
            except Exception as exc:
                log.warning(
                    "readiness check failed",
                    extra={"dependency": "embedding_model", "error_type": type(exc).__name__},
                )
                checks["embedding_model"] = {
                    "status": "unavailable",
                    "detail": "Configured embedding model metadata is unavailable.",
                }
                ready = False
            else:
                if stored_dimension != configured_dimension:
                    checks["chroma"] = {
                        "status": "incompatible",
                        "detail": "The collection embedding dimension does not match the configured model.",
                    }
                    ready = False
                    checks["embedding_model"] = {"status": "incompatible"}
                else:
                    checks["embedding_model"] = {"status": "ok"}
    else:
        checks["chroma"] = {"status": "not_required"}

    if settings.redis_url.strip():
        redis_client: Redis | None = None
        try:
            redis_client = Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=_REDIS_TIMEOUT_SECONDS,
                socket_timeout=_REDIS_TIMEOUT_SECONDS,
            )
            redis_client.ping()
            checks["redis"] = {"status": "ok"}
        except Exception as exc:
            log.warning(
                "readiness check failed",
                extra={"dependency": "redis", "error_type": type(exc).__name__},
            )
            checks["redis"] = {
                "status": "unavailable",
                "detail": "Redis is unreachable.",
            }
            ready = False
        finally:
            if redis_client is not None:
                try:
                    redis_client.close()
                except Exception as exc:
                    log.warning(
                        "readiness client cleanup failed",
                        extra={"dependency": "redis", "error_type": type(exc).__name__},
                    )
    else:
        checks["redis"] = {"status": "not_configured"}

    if not ready:
        response.status_code = 503
    return {"status": "ready" if ready else "not_ready", "checks": checks}
