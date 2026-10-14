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
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select, text
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
from sourcemind.models.connector import ArtifactLink
from sourcemind.models.document import Document, DocumentSourceType, IngestionStatus
from sourcemind.models.workspace import Workspace

log = structlog.get_logger(__name__)

_MAX_CONTENT_CHARS = 500_000
_IDEM_TTL = 60 * 60 * 24  # 24 hours
_IDEM_RESERVATION_TTL_MS = 30_000
_IDEM_WAIT_SECONDS = 2.0
_IDEM_POLL_SECONDS = 0.05

# Columns a connector may supply for the ArtifactLink written with the
# Document. Scope (workspace/document), the memory pointer and any identity
# resolution are set here or by the worker, never by the caller: the sync-time
# link always has resolved_user_id NULL (D-021).
_ARTIFACT_LINK_FIELDS = frozenset(
    {
        "source_tool",
        "source_type",
        "source_id",
        "source_url",
        "source_author",
        "source_timestamp",
        "artifact_metadata",
        # Revision handoff (contract "Package interfaces"): supplied by the
        # sync orchestrator, never by an end user.
        "source_revision",
        "connector_id",
        "connector_generation",
    }
)

# Failure codes eligible for the recovery sweep (contract C4/C6c).
_REQUEUE_ELIGIBLE_CODES = frozenset({"transient", "incomplete_extraction", "connector_paused"})
_REQUEUE_BACKOFF_BASE_SECONDS = 60
_REQUEUE_BACKOFF_MAX_SECONDS = 6 * 60 * 60

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
    ingestion_mode: str = "extract",
    idempotency_key: str,
    artifact_link: dict[str, Any] | None = None,
    attribution: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    pre_commit: Callable[[AsyncSession], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """
    Stage 1 entry point.

    ``context`` (connectors) holds contextual labels stored in
    ``pipeline_data["context"]``; they reach extraction as a separate,
    non-authored field and are never part of the stored text.
    ``pre_commit`` runs inside the handoff transaction after the Document,
    ArtifactLink and artifact head are written and before the commit; if it
    raises, nothing is committed.
    With ``artifact_link["connector_id"]`` set the handoff is revision-aware:
    see ``_receive_connector_revision``.

    ``artifact_link`` (connectors only) is inserted in the SAME transaction as
    the new Document, so it is durable before the ingestion task is published.
    ``attribution`` is the connector's sync-time attribution decision, stored
    as ``pipeline_data["attribution"]`` for the worker's credit-time recheck.
    Neither is written when an existing document is returned.

    Returns a dict compatible with IngestionJobResponse:
      job_id, document_id (UUID), status, message, already_exists
    """
    # ── Input validation ──────────────────────────────────────────
    if not content and not url:
        raise ValidationError("Either 'content' or 'url' must be provided.")
    if ingestion_mode not in {"extract", "verbatim"}:
        raise ValidationError("Unsupported ingestion mode.")
    if ingestion_mode == "verbatim" and (
        url is not None or content is None or not content.strip()
    ):
        raise ValidationError("Verbatim ingestion requires inline content.")

    if artifact_link is not None:
        unexpected = set(artifact_link) - _ARTIFACT_LINK_FIELDS
        if unexpected:
            raise ValidationError(
                "artifact_link may not set: " + ", ".join(sorted(unexpected))
            )

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

    if artifact_link and artifact_link.get("connector_id") is not None:
        # A connector handoff is deduplicated by the artifact head, which is authoritative and
        # revision-aware: the same content as the LATEST revision is a no-op (same_revision), but
        # content that differs from the latest revision is a new revision even if it equals an
        # OLDER one. A time-boxed cache keyed on the content alone cannot tell "replay" from
        # "the source went back to A", so a revert A -> B -> A used to be swallowed for 24 h.
        # The head row is locked by the handoff itself, so concurrent duplicates serialise there.
        return await _receive_reserved(
            session=session,
            workspace_id=workspace_id,
            user_id=user_id,
            content=content,
            url=url,
            source_type=source_type,
            title=title,
            tags=tags,
            category=category,
            ingestion_mode=ingestion_mode,
            idempotency_key=idempotency_key,
            artifact_link=artifact_link,
            attribution=attribution,
            context=context,
            pre_commit=pre_commit,
        )

    redis = get_redis()
    idem_key = _idempotency_cache_key(workspace_id, user_id, idempotency_key)
    fingerprint = _request_fingerprint(
        content=content,
        url=url,
        source_type=source_type,
        title=title,
        tags=tags,
        category=category,
        ingestion_mode=ingestion_mode,
    )
    reservation_token, cached_response = await _claim_idempotency(
        redis,
        idem_key,
        fingerprint,
    )
    if cached_response is not None:
        log.debug("receive_idempotent", idempotency_key=idempotency_key)
        if artifact_link and artifact_link.get("connector_id") is not None:
            # A connector replaying a revision it already handed off: say so, so it counts
            # as skipped instead of importing the artifact a second time.
            return {**cached_response, "already_exists": True, "reason": "idempotent_replay"}
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
            ingestion_mode=ingestion_mode,
            idempotency_key=idempotency_key,
            artifact_link=artifact_link,
            attribution=attribution,
            context=context,
            pre_commit=pre_commit,
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
    ingestion_mode: str,
    idempotency_key: str,
    artifact_link: dict[str, Any] | None = None,
    attribution: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    pre_commit: Callable[[AsyncSession], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    if artifact_link is not None and artifact_link.get("connector_id") is not None:
        try:
            return await _receive_connector_revision(
                session=session,
                workspace_id=workspace_id,
                user_id=user_id,
                content=content,
                url=url,
                source_type=source_type,
                title=title,
                tags=tags,
                category=category,
                ingestion_mode=ingestion_mode,
                idempotency_key=idempotency_key,
                artifact_link=artifact_link,
                attribution=attribution,
                context=context,
                pre_commit=pre_commit,
            )
        except BaseException:
            await session.rollback()
            raise

    raw_for_hash = url if url else content
    assert raw_for_hash is not None
    hash_input = raw_for_hash if ingestion_mode == "extract" else f"verbatim\0{raw_for_hash}"
    sha256 = hashlib.sha256(hash_input.encode()).hexdigest()

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
    pipeline_data: dict[str, Any] = {
        "raw_content": content,
        "idempotency_key": idempotency_key,
        "current_stage": "queued",
        # Carried to store_memories via the worker. Tags supplied on
        # the ingest request used to be dropped here, so every memory
        # was stored with tags=NULL.
        "tags": tags or [],
        "category": category,
        "ingestion_mode": ingestion_mode,
        "dispatch_state": "orphaned",
        "dispatch_attempts": 0,
    }
    if attribution is not None:
        pipeline_data["attribution"] = dict(attribution)
    if context is not None:
        pipeline_data["context"] = dict(context)
    doc = Document(
        workspace_id=workspace_id,
        submitter_id=user_id,
        title=title,
        source_type=source_type,
        source_url=url,
        sha256_hash=sha256,
        ingestion_status=IngestionStatus.PENDING,
        pipeline_data=pipeline_data,
        ingestion_job_id=job_id,
    )
    session.add(doc)
    await session.flush()

    if artifact_link is not None:
        # Same transaction as the Document and committed before publication,
        # so the worker can never run before the link exists. memory_id is
        # backfilled by the worker; resolved_user_id is written only by the
        # worker's attribution finalization (D-021).
        session.add(
            ArtifactLink(
                workspace_id=workspace_id,
                document_id=doc.id,
                memory_id=None,
                resolved_user_id=None,
                **artifact_link,
            )
        )
        await session.flush()

    if pre_commit is not None:
        try:
            await pre_commit(session)
        except BaseException:
            await session.rollback()
            raise

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


async def _receive_connector_revision(
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
    ingestion_mode: str,
    idempotency_key: str,
    artifact_link: dict[str, Any],
    attribution: dict[str, Any] | None,
    context: dict[str, Any] | None,
    pre_commit: Callable[[AsyncSession], Awaitable[None]] | None,
) -> dict[str, Any]:
    """Revision-aware handoff for a connector artifact (contract C6).

    The ``artifact_heads`` row is inserted on first sight and locked
    (``INSERT .. ON CONFLICT DO UPDATE`` takes the row lock). If the latest
    link already carries this ``source_revision`` the handoff is a no-op
    (``already_exists``, ``reason="same_revision"``; ``pre_commit`` still runs
    so the discovery position can advance atomically). Otherwise the next
    ``revision_seq`` is allocated under that lock.

    The content hash is salted with the source identity AND the revision
    number: identical text from two sources keeps both rows, and a revision
    that returns to earlier text (A -> B -> A) does not collide with the
    ``uq_documents_workspace_sha256`` constraint.
    """
    link = dict(artifact_link)
    try:
        tool = str(link["source_tool"])
        kind = str(link["source_type"])
        source_id = str(link["source_id"])
    except KeyError as exc:
        raise ValidationError(f"artifact_link requires {exc.args[0]}") from None
    connector_id = uuid.UUID(str(link["connector_id"]))
    link["connector_id"] = connector_id
    revision = link.get("source_revision")
    key = {"ws": str(workspace_id), "tool": tool, "kind": kind, "sid": source_id}

    head = (
        await session.execute(
            text(
                "INSERT INTO artifact_heads AS h "
                "(workspace_id, source_tool, source_type, source_id, latest_seq, published_seq) "
                "VALUES (CAST(:ws AS uuid), :tool, :kind, :sid, 0, 0) "
                "ON CONFLICT (workspace_id, source_tool, source_type, source_id) "
                "DO UPDATE SET updated_at = now() "
                "RETURNING latest_seq, published_seq, published_document_id"
            ),
            key,
        )
    ).one()
    latest_seq = int(head.latest_seq)

    previous_document_id: uuid.UUID | None = None
    if latest_seq > 0:
        previous = (
            await session.execute(
                text(
                    "SELECT document_id, source_revision, connector_id FROM artifact_links "
                    "WHERE workspace_id = CAST(:ws AS uuid) AND source_tool = :tool "
                    "AND source_type = :kind AND source_id = :sid "
                    "AND revision_seq = :seq ORDER BY created_at LIMIT 1"
                ),
                {**key, "seq": latest_seq},
            )
        ).first()
        if previous is not None:
            previous_document_id = previous.document_id
            # OWNERSHIP, decided under the artifact-head lock taken above and in the same
            # transaction that would allocate the next revision: a connector may not supersede, and
            # through publication retire, content whose latest revision belongs to ANOTHER connector
            # that is still live. (Overlapping connectors are rejected when they are created or
            # resumed; this is the guard for data that got here some other way, and for the race
            # window.) Reading the owner's status without a lock is safe: "disconnected" is
            # permanent: a stale read can only answer "still live", which refuses, never the
            # reverse.
            # A connector row this caller cannot see counts as live (fail closed).
            if previous.connector_id is not None and previous.connector_id != connector_id:
                owner_status = (
                    await session.execute(
                        text("SELECT status FROM connector_configs WHERE id = :c"),
                        {"c": previous.connector_id},
                    )
                ).scalar_one_or_none()
                if owner_status != "disconnected":
                    log.warning(
                        "connector_handoff_refused_owned_elsewhere",
                        workspace_id=str(workspace_id),
                        connector_id=str(connector_id),
                    )
                    if pre_commit is not None:
                        await pre_commit(session)
                    await session.commit()
                    if previous_document_id is None:
                        raise RuntimeError("latest revision has no document")
                    return await _same_revision_response(
                        session, previous_document_id, workspace_id, latest_seq,
                        reason="owned_by_other_connector",
                    )
            # "Same revision" is a statement about what THIS connector already handed off. A new
            # connector (a reconnect after a disconnect, possibly with "remove memories") that sees
            # identical content is a new revision: the earlier one's memories may have been removed
            # with the old connector, and treating the content as already imported would leave the
            # source silently absent forever (found by the browser journey).
            if (
                revision is not None
                and previous.source_revision == revision
                and previous.connector_id == connector_id
            ):
                if pre_commit is not None:
                    await pre_commit(session)
                await session.commit()
                if previous_document_id is None:
                    raise RuntimeError("latest revision has no document")
                return await _same_revision_response(
                    session, previous_document_id, workspace_id, latest_seq
                )

    new_seq = latest_seq + 1
    await session.execute(
        text(
            "UPDATE artifact_heads SET latest_seq = :n, updated_at = now() "
            "WHERE workspace_id = CAST(:ws AS uuid) AND source_tool = :tool "
            "AND source_type = :kind AND source_id = :sid"
        ),
        {**key, "n": new_seq},
    )
    supersedes = head.published_document_id or previous_document_id

    raw = url if url else content
    assert raw is not None
    salted = f"connector\0{tool}\0{kind}\0{source_id}\0{new_seq}\0{ingestion_mode}\0{raw}"
    sha256 = hashlib.sha256(salted.encode()).hexdigest()

    job_id = str(uuid.uuid4())
    pipeline_data: dict[str, Any] = {
        "raw_content": content,
        "idempotency_key": idempotency_key,
        "current_stage": "queued",
        "tags": tags or [],
        "category": category,
        "ingestion_mode": ingestion_mode,
        "dispatch_state": "orphaned",
        "dispatch_attempts": 0,
    }
    if attribution is not None:
        pipeline_data["attribution"] = dict(attribution)
    if context is not None:
        pipeline_data["context"] = dict(context)
    doc = Document(
        workspace_id=workspace_id,
        submitter_id=user_id,
        title=title,
        source_type=source_type,
        source_url=url,
        sha256_hash=sha256,
        ingestion_status=IngestionStatus.PENDING,
        pipeline_data=pipeline_data,
        ingestion_job_id=job_id,
        supersedes_document_id=supersedes,
        connector_id=connector_id,
        connector_generation=link.get("connector_generation"),
    )
    session.add(doc)
    await session.flush()
    session.add(
        ArtifactLink(
            workspace_id=workspace_id,
            document_id=doc.id,
            memory_id=None,
            resolved_user_id=None,
            revision_seq=new_seq,
            **link,
        )
    )
    await session.flush()

    if pre_commit is not None:
        await pre_commit(session)
    await session.commit()

    response = await _dispatch_or_return_document(
        session=session, document_id=doc.id, workspace_id=workspace_id
    )
    response["revision_seq"] = new_seq
    return response


async def _same_revision_response(
    session: AsyncSession,
    document_id: uuid.UUID,
    workspace_id: uuid.UUID,
    revision_seq: int,
    *,
    reason: str = "same_revision",
) -> dict[str, Any]:
    doc = (
        await session.execute(
            select(Document).where(
                Document.id == document_id, Document.workspace_id == workspace_id
            )
        )
    ).scalar_one()
    await session.commit()
    return {
        "job_id": doc.ingestion_job_id or "",
        "document_id": str(doc.id),
        "status": doc.ingestion_status,
        "message": (
            "Source revision already ingested."
            if reason == "same_revision"
            else "This source is imported by another connector."
        ),
        "memory_count": doc.memory_count,
        "already_exists": True,
        "reason": reason,
        "revision_seq": revision_seq,
    }


async def requeue_transient_failure(
    session: AsyncSession,
    document_id: uuid.UUID,
    workspace_id: uuid.UUID,
    *,
    now: datetime,
) -> bool:
    """Reset an eligible failed connector document to pending and re-dispatch it.

    Refuses (returns False, nothing changed) when the document is not a failed
    connector document, its error code is not ``transient`` /
    ``incomplete_extraction``, ``attempts`` reached
    ``connector_max_handoff_attempts``, or the connector is no longer active at
    the document's generation (checked under ``FOR SHARE``; a connector the
    caller cannot see counts as not active).
    On success: status ``pending``, ``attempts`` + 1, ``next_retry_at`` set by
    exponential backoff, and the document goes back through the receiver outbox.
    """
    doc = (
        await session.execute(
            select(Document)
            .where(
                Document.id == document_id,
                Document.workspace_id == workspace_id,
                Document.deleted_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if doc is None:
        await session.rollback()
        return False
    pipeline_data = dict(doc.pipeline_data or {})
    attempts = int(pipeline_data.get("attempts", 0))
    if (
        doc.ingestion_status != IngestionStatus.FAILED
        or doc.connector_id is None
        or pipeline_data.get("error_code") not in _REQUEUE_ELIGIBLE_CODES
        or attempts >= get_settings().connector_max_handoff_attempts
    ):
        await session.rollback()
        return False

    connector = (
        await session.execute(
            text(
                "SELECT status, generation FROM connector_configs "
                "WHERE id = CAST(:c AS uuid) AND workspace_id = CAST(:w AS uuid) FOR SHARE"
            ),
            {"c": str(doc.connector_id), "w": str(workspace_id)},
        )
    ).first()
    if (
        connector is None
        or connector.status != "active"
        or connector.generation != doc.connector_generation
    ):
        await session.rollback()
        return False

    attempts += 1
    backoff = min(
        _REQUEUE_BACKOFF_BASE_SECONDS * (2**attempts), _REQUEUE_BACKOFF_MAX_SECONDS
    )
    pipeline_data.pop("error_code", None)
    pipeline_data.update(
        {
            "attempts": attempts,
            "next_retry_at": (now.astimezone(UTC) + timedelta(seconds=backoff)).isoformat(),
            "current_stage": "queued",
            "dispatch_state": "orphaned",
            "dispatch_attempts": 0,
        }
    )
    doc.pipeline_data = pipeline_data
    doc.ingestion_status = IngestionStatus.PENDING
    doc.error_message = None
    doc.ingestion_job_id = str(uuid.uuid4())
    await session.commit()

    await _dispatch_or_return_document(
        session=session, document_id=document_id, workspace_id=workspace_id
    )
    return True


async def recover_pending_dispatch(
    session: AsyncSession,
    document_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> dict[str, Any]:
    """Re-dispatch an already-committed document by id, without re-receiving it.

    For connectors (D-021): the ArtifactLink commits with its Document before
    the task is published, so a publication failure leaves a committed link
    over a document that may never have been queued. Recovery goes through the
    same row-locked dispatch state machine as receive(): the original
    ``ingestion_job_id`` is reused as the task id, a ``queued`` document is not
    republished, and a broker error marks it ``uncertain`` and re-raises.
    It never re-reads content, so it cannot create a second Document or a
    second attribution decision.
    """
    return await _dispatch_or_return_document(
        session=session,
        document_id=document_id,
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
    ingestion_mode: str = "extract",
) -> str:
    request = {
        "category": category,
        "content": content,
        "source_type": source_type,
        "tags": tags or [],
        "title": title,
        "url": url,
    }
    if ingestion_mode != "extract":
        request["ingestion_mode"] = ingestion_mode
    payload = json.dumps(
        request,
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
