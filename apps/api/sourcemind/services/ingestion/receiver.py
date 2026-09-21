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

import asyncio
import hashlib
import json
import time
import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.config import get_settings
from sourcemind.core.exceptions import (
    ContentTooLargeError,
    IdempotencyConflictError,
    ServiceUnavailableError,
    ValidationError,
    WorkspaceNotFoundError,
)
from sourcemind.core.redis_client import get_redis
from sourcemind.core.url_security import validate_public_url, validate_url_syntax
from sourcemind.models.document import Document, DocumentSourceType, IngestionStatus
from sourcemind.models.workspace import Workspace

log = structlog.get_logger(__name__)

_MAX_CONTENT_CHARS = 500_000
_IDEM_TTL = 60 * 60 * 24  # 24 hours
_IDEM_RESERVATION_TTL_MS = 30_000
_IDEM_WAIT_SECONDS = 2.0
_IDEM_POLL_SECONDS = 0.05

_RESERVE_IDEMPOTENCY = """
local key = KEYS[1]
local fingerprint = ARGV[1]
local token = ARGV[2]
local reservation_ttl_ms = tonumber(ARGV[3])

if redis.call('EXISTS', key) == 0 then
  redis.call(
    'HSET', key,
    'state', 'in_progress',
    'fingerprint', fingerprint,
    'token', token
  )
  redis.call('PEXPIRE', key, reservation_ttl_ms)
  return {'reserved', '', reservation_ttl_ms}
end

local existing_fingerprint = redis.call('HGET', key, 'fingerprint')
if existing_fingerprint ~= fingerprint then
  return {'conflict', '', redis.call('PTTL', key)}
end

local state = redis.call('HGET', key, 'state')
local remaining_ms = redis.call('PTTL', key)
if state == 'completed' then
  return {'completed', redis.call('HGET', key, 'response') or '', remaining_ms}
end
if remaining_ms < 0 then
  redis.call('PEXPIRE', key, reservation_ttl_ms)
  remaining_ms = reservation_ttl_ms
end
return {'in_progress', '', remaining_ms}
"""

_COMPLETE_IDEMPOTENCY = """
if redis.call('HGET', KEYS[1], 'state') == 'in_progress'
  and redis.call('HGET', KEYS[1], 'fingerprint') == ARGV[1]
  and redis.call('HGET', KEYS[1], 'token') == ARGV[2] then
  redis.call('HSET', KEYS[1], 'state', 'completed', 'response', ARGV[3])
  redis.call('HDEL', KEYS[1], 'token')
  redis.call('EXPIRE', KEYS[1], ARGV[4])
  return 1
end
return 0
"""

_RELEASE_IDEMPOTENCY = """
if redis.call('HGET', KEYS[1], 'state') == 'in_progress'
  and redis.call('HGET', KEYS[1], 'token') == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


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
    category: str | None = None,
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
        validate_url_syntax(url)
        if not get_settings().url_ingestion_enabled:
            raise ValidationError("URL ingestion is disabled.")
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

    redis = get_redis()
    idem_key = _idempotency_cache_key(workspace_id, user_id, idempotency_key)
    fingerprint = _request_fingerprint(
        content=content,
        url=url,
        source_type=source_type,
        title=title,
        tags=tags,
        category=category,
    )
    reservation_token, cached_response = await _claim_idempotency(
        redis,
        idem_key,
        fingerprint,
    )
    if cached_response is not None:
        log.debug("receive_idempotent", idempotency_key=idempotency_key)
        return cached_response
    assert reservation_token is not None

    try:
        response = await _receive_reserved(
            session=session,
            workspace_id=workspace_id,
            user_id=user_id,
            content=content,
            url=url,
            source_type=source_type,
            title=title,
            tags=tags,
            category=category,
            idempotency_key=idempotency_key,
        )
        completed = await redis.eval(
            _COMPLETE_IDEMPOTENCY,
            1,
            idem_key,
            fingerprint,
            reservation_token,
            json.dumps(response, sort_keys=True, separators=(",", ":")),
            _IDEM_TTL,
        )
        if not completed:
            log.warning(
                "receive_idempotency_reservation_lost",
                workspace_id=str(workspace_id),
                user_id=str(user_id),
            )
        return response
    except BaseException:
        try:
            await redis.eval(
                _RELEASE_IDEMPOTENCY,
                1,
                idem_key,
                reservation_token,
            )
        except Exception as release_error:
            log.warning(
                "receive_idempotency_release_failed",
                error_type=type(release_error).__name__,
            )
        raise


async def _receive_reserved(
    *,
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    content: str | None,
    url: str | None,
    source_type: str,
    title: str | None,
    tags: list[str] | None,
    category: str | None,
    idempotency_key: str,
) -> dict[str, Any]:
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
        return await _dispatch_or_return_document(
            session=session,
            document_id=existing_doc.id,
            workspace_id=workspace_id,
        )

    job_id = str(uuid.uuid4())
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
            "category": category,
            "dispatch_state": "orphaned",
            "dispatch_attempts": 0,
        },
        ingestion_job_id=job_id,
    )
    session.add(doc)
    await session.flush()

    # The worker is a different process on a different connection, and it
    # picks the task up within milliseconds. Until this transaction commits,
    # the document does not exist as far as that connection is concerned.
    # A document must be durable before anything is scheduled against it.
    # The preassigned polling id and orphaned dispatch state are durable before
    # publication. If publication reports an error, recovery can reuse that id;
    # the later result is still treated as ambiguous rather than as proof that
    # the broker rejected the task.
    await session.commit()

    return await _dispatch_or_return_document(
        session=session,
        document_id=doc.id,
        workspace_id=workspace_id,
    )


async def _dispatch_or_return_document(
    *,
    session: AsyncSession,
    document_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> dict[str, Any]:
    """Return existing work or serialize recovery of an orphaned dispatch.

    The task id is committed before publication so every successful response
    is pollable. The row lock prevents simultaneous retries from publishing the
    same recovery independently. A broker exception remains ambiguous: the
    broker may have accepted the message before the client observed the error,
    so recovery reuses the same task id but does not claim exactly-once delivery.
    """
    while True:
        locked_result = await session.execute(
            select(Document)
            .where(
                Document.id == document_id,
                Document.workspace_id == workspace_id,
                Document.deleted_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        doc = locked_result.scalar_one()
        pipeline_data = dict(doc.pipeline_data or {})
        current_stage = pipeline_data.get("current_stage", doc.ingestion_status)
        pending_dispatch = (
            doc.ingestion_status == IngestionStatus.PENDING
            and current_stage in {"pending", "queued"}
        )

        if doc.ingestion_job_id is None:
            doc.ingestion_job_id = str(uuid.uuid4())
            pipeline_data["dispatch_state"] = (
                "orphaned" if pending_dispatch else "not_required"
            )
            pipeline_data.setdefault("dispatch_attempts", 0)
            doc.pipeline_data = pipeline_data
            await session.commit()
            if pending_dispatch:
                continue
            return _document_response(doc, existing=True)

        dispatch_state = pipeline_data.get("dispatch_state")
        if not pending_dispatch:
            if dispatch_state in {"orphaned", "publishing", "uncertain"}:
                pipeline_data["dispatch_state"] = "work_started"
                doc.pipeline_data = pipeline_data
            await session.commit()
            return _document_response(doc, existing=True)

        if dispatch_state is None:
            pipeline_data["dispatch_state"] = "queued"
            pipeline_data.setdefault("dispatch_attempts", 1)
            doc.pipeline_data = pipeline_data
            await session.commit()
            return _document_response(doc, existing=True)

        if dispatch_state == "queued":
            await session.commit()
            return _document_response(doc, existing=True)

        pipeline_data["dispatch_state"] = "publishing"
        pipeline_data["dispatch_attempts"] = int(
            pipeline_data.get("dispatch_attempts", 0)
        ) + 1
        doc.pipeline_data = pipeline_data
        await session.flush()

        from sourcemind.workers.ingestion import process_document

        try:
            process_document.apply_async(
                task_id=doc.ingestion_job_id,
                kwargs={
                    "document_id": str(doc.id),
                    "workspace_id": str(workspace_id),
                    "user_id": str(doc.submitter_id),
                },
                priority=5,
            )
        except Exception:
            pipeline_data = {**pipeline_data, "dispatch_state": "uncertain"}
            doc.pipeline_data = pipeline_data
            await session.commit()
            log.warning(
                "document_dispatch_uncertain",
                document_id=str(doc.id),
                job_id=doc.ingestion_job_id,
                workspace_id=str(workspace_id),
                attempts=pipeline_data["dispatch_attempts"],
            )
            raise

        pipeline_data = {**pipeline_data, "dispatch_state": "queued"}
        doc.pipeline_data = pipeline_data
        await session.commit()

        log.info(
            "document_received",
            document_id=str(doc.id),
            job_id=doc.ingestion_job_id,
            workspace_id=str(workspace_id),
            source_type=doc.source_type,
            attempts=pipeline_data["dispatch_attempts"],
        )
        return _document_response(doc, existing=False)


def _document_response(doc: Document, *, existing: bool) -> dict[str, Any]:
    assert doc.ingestion_job_id is not None
    return {
        "job_id": doc.ingestion_job_id,
        "document_id": str(doc.id),
        "status": doc.ingestion_status,
        "message": (
            "Document already ingested. Returning existing record."
            if existing
            else "Document queued for ingestion. "
            "Poll /v1/memories/jobs/:job_id for status."
        ),
        "memory_count": doc.memory_count,
        "already_exists": existing,
    }


def _idempotency_cache_key(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    idempotency_key: str,
) -> str:
    return f"idem:receive:v2:{workspace_id}:{user_id}:{idempotency_key}"


def _request_fingerprint(
    *,
    content: str | None,
    url: str | None,
    source_type: str,
    title: str | None,
    tags: list[str] | None,
    category: str | None,
) -> str:
    payload = json.dumps(
        {
            "category": category,
            "content": content,
            "source_type": source_type,
            "tags": tags or [],
            "title": title,
            "url": url,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


async def _claim_idempotency(
    redis: Any,
    key: str,
    fingerprint: str,
) -> tuple[str | None, dict[str, Any] | None]:
    """Claim a short lease or briefly await an identical request's result.

    This coordinates live callers but does not guarantee exactly-once delivery.
    A process that outlives its lease can overlap a retry, so database and
    downstream idempotency controls remain necessary.
    """
    token = uuid.uuid4().hex
    deadline = time.monotonic() + _IDEM_WAIT_SECONDS
    while True:
        state_raw, response_raw, remaining_ms_raw = await redis.eval(
            _RESERVE_IDEMPOTENCY,
            1,
            key,
            fingerprint,
            token,
            _IDEM_RESERVATION_TTL_MS,
        )
        state = str(state_raw)
        if state == "reserved":
            return token, None
        if state == "conflict":
            raise IdempotencyConflictError(
                "The Idempotency-Key was already used with a different request body."
            )
        if state == "completed":
            return None, json.loads(str(response_raw))

        remaining_wait = deadline - time.monotonic()
        if remaining_wait <= 0:
            remaining_ms = max(1, int(remaining_ms_raw))
            raise ServiceUnavailableError(
                "An identical ingestion request is still being processed.",
                details={"retry_after_seconds": max(1, (remaining_ms + 999) // 1000)},
            )
        await asyncio.sleep(min(_IDEM_POLL_SECONDS, remaining_wait))


async def _validate_url(url: str) -> None:
    """Validate URL syntax and DNS targets without issuing an HTTP request."""
    await validate_public_url(url)
