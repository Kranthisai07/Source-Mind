"""
Stage 1 — RECEIVE: Validate input, deduplicate, create document record,
enqueue Celery task, return job_id immediately.

Error codes:
  SM010 — missing content/url
  SM011 — invalid content type
  SM012 — content too large
  SM022 — workspace not found
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.exceptions import (
    ContentTooLargeError,
    ValidationError,
    WorkspaceNotFoundError,
)
from sourcemind.core.redis_client import get_redis
from sourcemind.models.document import Document, DocumentSourceType, IngestionStatus
from sourcemind.models.workspace import Workspace

log = structlog.get_logger(__name__)

_MAX_CONTENT_CHARS = 500_000
_IDEM_TTL = 60 * 60 * 24  # 24 hours


async def receive(
    *,
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    content: str | None = None,
    url: str | None = None,
    source_type: str = DocumentSourceType.TEXT,
    title: str | None = None,
    tags: list[str] | None = None,
    idempotency_key: str,
) -> dict[str, Any]:
    """
    Stage 1 entry point.

    Returns a dict compatible with IngestionJobResponse:
      job_id, document_id (UUID), status, message, already_exists
    """
    # ── Input validation ──────────────────────────────────────────
    if not content and not url:
        raise ValidationError("Either 'content' or 'url' must be provided.")

    if content and len(content) > _MAX_CONTENT_CHARS:
        raise ContentTooLargeError(
            f"Content exceeds {_MAX_CONTENT_CHARS:,} character limit "
            f"(got {len(content):,} chars)."
        )

    if url:
        await _validate_url(url)
        source_type = DocumentSourceType.URL

    # ── Workspace existence check ─────────────────────────────────
    ws_result = await session.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.deleted_at.is_(None),
        )
    )
    if not ws_result.scalar_one_or_none():
        raise WorkspaceNotFoundError(f"Workspace {workspace_id} not found.")

    # ── Idempotency check ─────────────────────────────────────────
    redis = get_redis()
    idem_key = f"idem:receive:{idempotency_key}"
    cached = await redis.get(idem_key)
    if cached:
        log.debug("receive_idempotent", idempotency_key=idempotency_key)
        return json.loads(cached)

    # ── SHA-256 deduplication ─────────────────────────────────────
    raw_for_hash = url if url else content
    assert raw_for_hash is not None
    sha256 = hashlib.sha256(raw_for_hash.encode()).hexdigest()

    dup_result = await session.execute(
        select(Document).where(
            Document.workspace_id == workspace_id,
            Document.sha256_hash == sha256,
            Document.deleted_at.is_(None),
        )
    )
    existing_doc = dup_result.scalar_one_or_none()

    if existing_doc is not None:
        log.info(
            "document_duplicate",
            document_id=str(existing_doc.id),
            workspace_id=str(workspace_id),
        )
        resp: dict[str, Any] = {
            "job_id": existing_doc.ingestion_job_id or str(existing_doc.id),
            "document_id": str(existing_doc.id),
            "status": existing_doc.ingestion_status,
            "message": "Document already ingested. Returning existing record.",
            "memory_count": existing_doc.memory_count,
            "already_exists": True,
        }
        await redis.setex(idem_key, _IDEM_TTL, json.dumps(resp))
        return resp

    # ── Create document record ────────────────────────────────────
    doc = Document(
        workspace_id=workspace_id,
        submitter_id=user_id,
        title=title,
        source_type=source_type,
        source_url=url,
        sha256_hash=sha256,
        ingestion_status=IngestionStatus.PENDING,
        pipeline_data={
            "raw_content": content,
            "idempotency_key": idempotency_key,
            "current_stage": "queued",
            # Carried to store_memories via the worker. Tags supplied on
            # the ingest request used to be dropped here, so every memory
            # was stored with tags=NULL.
            "tags": tags or [],
        },
    )
    session.add(doc)
    await session.flush()  # generate doc.id

    # ── Enqueue Celery task ───────────────────────────────────────
    from sourcemind.workers.ingestion import process_document

    task = process_document.apply_async(
        kwargs={
            "document_id": str(doc.id),
            "workspace_id": str(workspace_id),
            "user_id": str(user_id),
        },
        priority=5,
    )

    doc.ingestion_job_id = task.id
    await session.flush()

    log.info(
        "document_received",
        document_id=str(doc.id),
        job_id=task.id,
        workspace_id=str(workspace_id),
        source_type=source_type,
    )

    resp = {
        "job_id": task.id,
        "document_id": str(doc.id),
        "status": IngestionStatus.PENDING,
        "message": "Document queued for ingestion. Poll /v1/memories/jobs/:job_id for status.",
        "memory_count": None,
        "already_exists": False,
    }
    await redis.setex(idem_key, _IDEM_TTL, json.dumps(resp))
    return resp


async def _validate_url(url: str) -> None:
    """Validate URL format and basic reachability (HEAD request, 5s timeout)."""
    if not url.startswith(("http://", "https://")):
        raise ValidationError("URL must start with http:// or https://")

    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            response = await client.head(url)
            if response.status_code >= 400:
                raise ValidationError(
                    f"URL returned HTTP {response.status_code}. "
                    "Ensure the URL is publicly accessible."
                )
    except httpx.TimeoutException as exc:
        raise ValidationError("URL did not respond within 5 seconds.") from exc
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError(f"URL validation failed: {exc}") from exc
