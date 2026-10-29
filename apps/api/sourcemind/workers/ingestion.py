"""
Celery task: process_document — orchestrates the full 7-stage ingestion pipeline.

Stage sequence:
  1. RECEIVE    (sync, in API route — document already created)
  2. EXTRACT    — raw content → clean text + metadata
  3. CHUNK      — clean text → overlapping chunks
  4. FACT EXTRACT — chunks → atomic memory strings (Claude)
  5. EMBED      — memory strings → vector(3072) (OpenAI)
  6. ATTRIBUTE  — create initial attribution records
  7. INDEX & RELATE — write memories, detect relations, detect conflicts

Retry policy: max 3 attempts, 60s / 120s / 240s backoff.
Status updates written to documents.ingestion_status after each stage.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import structlog

from sourcemind.services.graph.outbox import emit_memory_states
from sourcemind.workers.celery_app import app as celery_app

log = structlog.get_logger(__name__)


@celery_app.task(
    bind=True,
    name="sourcemind.workers.ingestion.process_document",
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
    reject_on_worker_lost=True,
    serializer="json",
)
def process_document(
    self: object,
    *,
    document_id: str,
    workspace_id: str,
    user_id: str,
) -> dict:
    """Synchronous Celery task. Delegates all async work to asyncio.run()."""
    return asyncio.run(_run_pipeline(self, document_id, workspace_id, user_id))


async def _run_pipeline(task: object, document_id: str, workspace_id: str, user_id: str) -> dict:
    """Execute all pipeline stages for a single document."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from sourcemind.core.config import get_settings
    from sourcemind.core.database import set_rls_user_context
    from sourcemind.core.dependencies import (
        WorkspacePermission,
        require_workspace_permission,
    )
    from sourcemind.core.exceptions import (
        WorkspaceAccessDeniedError,
        WorkspaceNotFoundError,
    )
    from sourcemind.core.redis_client import close_redis, init_redis
    from sourcemind.models.document import Document, IngestionStatus
    from sourcemind.services.attribution.engine import create_initial_attribution
    from sourcemind.services.attribution.github_links import (
        finalize_external_attribution,
        recheck_external_credit,
    )
    from sourcemind.services.ingestion.chunker import chunk
    from sourcemind.services.ingestion.embedder import EmbeddingService
    from sourcemind.services.ingestion.extractor import extract
    from sourcemind.services.ingestion.fact_extractor import FactExtractor
    from sourcemind.services.memory.relations import RelationDetector
    from sourcemind.services.memory.store import (
        backfill_artifact_links,
        build_memories,
        merge_pipeline_data,
        persist_memories,
        store_memories,
        update_document_status,
    )

    settings = get_settings()

    # Instantiate AI clients once per task (not inside each stage)
    from anthropic import AsyncAnthropic
    from openai import AsyncOpenAI

    openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    anthropic_client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    embedder = EmbeddingService(openai_client)
    fact_extractor = FactExtractor(anthropic_client)
    relation_detector = RelationDetector(anthropic_client)

    # Dedicated engine for this Celery worker process. Must use
    # settings.async_database_url, not settings.database_url: deriving the URL
    # inline here is exactly how this module ended up without the bare
    # postgresql:// normalisation, failing every task with
    # "The asyncio extension requires an async driver" while the API stayed
    # healthy.
    engine = create_async_engine(
        settings.async_database_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
    )
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    await init_redis()

    doc_uuid = uuid.UUID(document_id)
    ws_uuid = uuid.UUID(workspace_id)
    user_uuid = uuid.UUID(user_id)
    start = datetime.now(UTC)

    try:
        async with factory() as session:
            await set_rls_user_context(session, user_uuid)
            try:
                await require_workspace_permission(
                    session,
                    user_uuid,
                    ws_uuid,
                    WorkspacePermission.CONTRIBUTE,
                )
            except (WorkspaceAccessDeniedError, WorkspaceNotFoundError):
                log.warning(
                    "pipeline_membership_revoked",
                    document_id=document_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                )
                return {"status": "rejected", "error": "Workspace access revoked"}

            document_filters = (
                Document.id == doc_uuid,
                Document.workspace_id == ws_uuid,
                Document.submitter_id == user_uuid,
            )
            result = await session.execute(
                select(Document)
                .where(*document_filters)
                # FOR NO KEY UPDATE, not FOR UPDATE: this claim is held for the whole
                # pipeline, model calls included. A newer revision's document references
                # this one (supersedes_document_id), and the foreign-key re-check on its
                # own status updates takes FOR KEY SHARE on this row, which only a full
                # FOR UPDATE conflicts with. NO KEY UPDATE still excludes a second claimer.
                .with_for_update(skip_locked=True, key_share=True)
                .execution_options(populate_existing=True)
            )
            doc = result.scalar_one_or_none()
            if not doc:
                existing_document_id = await session.scalar(
                    select(Document.id).where(*document_filters)
                )
                if existing_document_id is not None:
                    log.info(
                        "pipeline_duplicate_in_progress",
                        document_id=document_id,
                        workspace_id=workspace_id,
                    )
                    return {
                        "status": "in_progress",
                        "document_id": document_id,
                    }
                log.error("pipeline_doc_not_found", document_id=document_id)
                return {"status": "failed", "error": "Document not found"}

            if doc.ingestion_status == IngestionStatus.COMPLETED:
                return {
                    "status": "completed",
                    "document_id": document_id,
                    "memories_created": doc.memory_count,
                    "already_completed": True,
                }
            if doc.ingestion_status == IngestionStatus.FAILED:
                return {
                    "status": "failed",
                    "document_id": document_id,
                    "memories_created": doc.memory_count,
                    "already_terminal": True,
                    "error": doc.error_message,
                }

            base_pipeline_data = dict(doc.pipeline_data or {})
            attempt_savepoint = await session.begin_nested()
            try:
                pipeline_data = doc.pipeline_data or {}
                raw_content = pipeline_data.get("raw_content")
                verbatim = pipeline_data.get("ingestion_mode") == "verbatim"
                is_connector = getattr(doc, "connector_id", None) is not None
                context_text = _context_text(pipeline_data.get("context"))
                extra: dict = {}  # merged into pipeline_data on the way out

                # C8: a disconnected connector must cost nothing. Checked before
                # any provider or embedding call.
                if is_connector:
                    blocked = await _connector_block_reason(
                        session, doc, ws_uuid, keep_lock=False
                    )
                    if blocked:
                        return await _fail_disconnected(
                            session, doc_uuid, document_id, start, reason=blocked
                        )

                if verbatim:
                    if not isinstance(raw_content, str) or not raw_content.strip():
                        raise ValueError("verbatim ingestion requires non-empty raw_content")
                    facts = [raw_content]
                    chunk_count = 1
                    fact_extraction = None
                    log.info("pipeline_verbatim", document_id=document_id)
                else:
                    if is_connector and isinstance(raw_content, str):
                        if len(raw_content) > settings.connector_max_artifact_chars:
                            extra.setdefault("truncated", {})["chars"] = len(raw_content)
                            raw_content = raw_content[: settings.connector_max_artifact_chars]

                    # Stage 2: EXTRACT
                    await update_document_status(
                        session,
                        doc_uuid,
                        IngestionStatus.PROCESSING,
                        current_stage="extracting",
                    )
                    document_extraction = await extract(
                        content=raw_content,
                        url=doc.source_url if doc.source_type == "url" else None,
                        source_type=doc.source_type,
                    )

                    # Stage 3: CHUNK
                    await update_document_status(
                        session,
                        doc_uuid,
                        IngestionStatus.PROCESSING,
                        current_stage="chunking",
                    )
                    chunks = await chunk(document_extraction)
                    if is_connector and len(chunks) > settings.connector_max_chunks_per_artifact:
                        extra.setdefault("truncated", {})["chunks"] = len(chunks)
                        chunks = chunks[: settings.connector_max_chunks_per_artifact]
                    chunk_count = len(chunks)
                    log.info("pipeline_chunked", document_id=document_id, chunks=chunk_count)

                    # Daily extraction budget (connector documents only), before
                    # the first provider call.
                    reserved_calls = 0
                    if is_connector and chunk_count > 0:
                        # Worst case is two provider calls per chunk (the empty/failed answer
                        # is retried once), so that is what is reserved; the unused part is
                        # refunded once the real call count is known.
                        reserved_calls = chunk_count * MAX_CALLS_PER_CHUNK
                        if not await _reserve_budget(
                            ws_uuid, reserved_calls, settings.workspace_daily_extraction_budget
                        ):
                            return await _fail_budget(
                                session, doc, doc_uuid, document_id, ws_uuid, extra, start
                            )

                    # Stage 4: FACT EXTRACTION
                    await update_document_status(
                        session,
                        doc_uuid,
                        IngestionStatus.PROCESSING,
                        current_stage="extracting_facts",
                    )
                    extract_kwargs: dict = {}
                    if context_text:
                        extract_kwargs["context"] = context_text
                    fact_extraction = await fact_extractor.extract(
                        chunks,
                        source_url=doc.source_url,
                        content_type=doc.source_type,
                        **extract_kwargs,
                    )
                    facts = fact_extraction.facts
                    usage = getattr(fact_extraction, "usage", None)
                    if usage:
                        extra["usage"] = usage
                    if reserved_calls and isinstance(usage, dict) and "calls" in usage:
                        await _refund_budget(ws_uuid, reserved_calls - int(usage["calls"]))
                    log.info(
                        "pipeline_facts",
                        document_id=document_id,
                        facts=len(facts),
                        failed_chunks=fact_extraction.failed_chunks,
                    )

                # A document with nothing to extract and one whose extraction
                # broke are different events and must not land in the same
                # state. Both used to be recorded as COMPLETED with
                # memory_count=0 and error_message NULL, which made a total
                # extraction failure indistinguishable from spam - and left no
                # way to tell how much of an apparently empty corpus was
                # actually flakiness.
                incomplete = fact_extraction is not None and fact_extraction.failed_chunks > 0
                if fact_extraction is not None and (
                    fact_extraction.wholly_failed or (is_connector and incomplete)
                ):
                    detail = "; ".join(fact_extraction.failure_reasons)[:480]
                    log.error(
                        "pipeline_extraction_failed",
                        document_id=document_id,
                        failed_chunks=fact_extraction.failed_chunks,
                        total_chunks=fact_extraction.total_chunks,
                        detail=detail,
                    )
                    code = "incomplete_extraction" if is_connector else "transient"
                    await update_document_status(
                        session,
                        doc_uuid,
                        IngestionStatus.FAILED,
                        memory_count=0,
                        chunk_count=chunk_count,
                        error_message=f"fact extraction failed: {detail}",
                        current_stage="failed",
                    )
                    await merge_pipeline_data(
                        session, doc_uuid, {**extra, **_failure_patch(pipeline_data, code)}
                    )
                    await session.commit()
                    return {
                        "status": "failed",
                        "document_id": document_id,
                        "memories_created": 0,
                        "error": "fact extraction failed",
                        "error_code": code,
                        "duration_ms": _elapsed_ms(start),
                    }

                if not facts:
                    # Genuinely nothing to extract: every chunk parsed cleanly
                    # and the model reported no facts, twice.
                    log.info(
                        "pipeline_no_facts",
                        document_id=document_id,
                        chunks=fact_extraction.total_chunks if fact_extraction else chunk_count,
                        reason="model returned no facts on both attempts",
                    )
                    publication = None
                    if is_connector:
                        # Publishes nothing and keeps the prior revision's
                        # memories live (C6d); an older revision is just
                        # superseded.
                        state, _head = await _lock_for_publication(session, doc, ws_uuid)
                        if state in _BLOCKED_STATES:
                            return await _fail_disconnected(
                                session, doc_uuid, document_id, start, reason=state
                            )
                        publication = "superseded_before_publish" if state == "superseded" else (
                            "noop" if state == "noop" else "no_facts"
                        )
                        if publication == "no_facts":
                            log.warning("pipeline_no_facts_kept_prior", document_id=document_id)
                        extra["publication"] = publication
                    await update_document_status(
                        session,
                        doc_uuid,
                        IngestionStatus.COMPLETED,
                        memory_count=0,
                        chunk_count=chunk_count,
                        current_stage="completed",
                    )
                    if extra:
                        await merge_pipeline_data(session, doc_uuid, extra)
                    await session.commit()
                    return {
                        "status": "completed",
                        "document_id": document_id,
                        "memories_created": 0,
                        "duration_ms": _elapsed_ms(start),
                        **({"publication": publication} if publication else {}),
                    }

                if fact_extraction is not None and fact_extraction.failed_chunks:
                    # Partial: some chunks produced facts, others broke. The
                    # document is usable, but incomplete, and says so.
                    log.warning(
                        "pipeline_partial_extraction",
                        document_id=document_id,
                        failed_chunks=fact_extraction.failed_chunks,
                        total_chunks=fact_extraction.total_chunks,
                    )

                # Stage 5: EMBED
                await update_document_status(
                    session, doc_uuid, IngestionStatus.PROCESSING, current_stage="embedding"
                )
                embedding_results = await embedder.embed(facts)

                # Stages 6+7: ATTRIBUTE + INDEX
                await update_document_status(
                    session, doc_uuid, IngestionStatus.PROCESSING, current_stage="indexing"
                )
                # Pass the ingestion metadata through instead of {}. Tags
                # travel request -> pipeline_data -> here -> Memory.tags.
                source_metadata = {
                    "tags": (doc.pipeline_data or {}).get("tags") or [],
                    "category": (doc.pipeline_data or {}).get("category"),
                }
                head = None
                if is_connector:
                    # Memories are built (not persisted) so relation planning,
                    # a model call, runs before any publication lock; they are
                    # persisted only after the connector/head locks decide.
                    memories = build_memories(
                        ws_uuid, doc_uuid, embedding_results, source_metadata
                    )
                else:
                    memories = await store_memories(
                        session, ws_uuid, doc_uuid, embedding_results, source_metadata
                    )

                # Relation classification is the LAST provider work. It runs
                # before the link lock below so the FOR SHARE lock is held only
                # across database statements, never across a model call
                # (D-021). plan() writes nothing; apply() further down writes
                # conflicts/relations after attribution exists, because
                # conflict creation reads each memory's attribution rows.
                relation_plan = await relation_detector.plan(session, memories, ws_uuid)

                # ── DB-only tail: no provider call from here to commit ──
                if is_connector:
                    # C6 / C8 / C10: connector row FOR SHARE, then the artifact
                    # head FOR UPDATE, before any memory row exists.
                    state, head = await _lock_for_publication(session, doc, ws_uuid)
                    if state in _BLOCKED_STATES:
                        return await _fail_disconnected(
                            session, doc_uuid, document_id, start, reason=state
                        )
                    if state in {"superseded", "noop"}:
                        publication = (
                            "superseded_before_publish" if state == "superseded" else "noop"
                        )
                        extra["publication"] = publication
                        await update_document_status(
                            session,
                            doc_uuid,
                            IngestionStatus.COMPLETED,
                            memory_count=0,
                            chunk_count=chunk_count,
                            current_stage="completed",
                        )
                        await merge_pipeline_data(session, doc_uuid, extra)
                        await session.commit()
                        return {
                            "status": "completed",
                            "document_id": document_id,
                            "memories_created": 0,
                            "publication": publication,
                            "duration_ms": _elapsed_ms(start),
                        }
                    await persist_memories(session, memories)

                # Who is credited. A direct upload credits its submitter. A
                # connector document (D-021) credits ONLY the admin-linked
                # author, rechecked now under FOR SHARE; the submitter is the
                # sync initiator and is never credited for it. Unresolved
                # authors get no attribution row. Any attribution record at
                # all marks a connector document; a malformed one fails closed
                # (no numeric id -> unresolved), never to the submitter.
                external = (doc.pipeline_data or {}).get("attribution")
                decision = None
                credit_user: uuid.UUID | None = user_uuid
                if external is not None:
                    decision = await recheck_external_credit(
                        session, ws_uuid, external if isinstance(external, dict) else {}
                    )
                    credit_user = decision.user_id

                if credit_user is not None:
                    for memory in memories:
                        await create_initial_attribution(
                            session, memory.id, credit_user, memory.content, doc.source_type
                        )

                # Connector-sourced documents have a pending ArtifactLink whose
                # memory_id could not be set at sync time — fill it in now.
                await backfill_artifact_links(session, doc_uuid, memories)

                if decision is not None:
                    # After the backfill, so the anchor AND its clones carry
                    # the final resolved_user_id, in this same transaction.
                    await finalize_external_attribution(session, doc_uuid, decision)

                await relation_detector.apply(session, memories, relation_plan)

                if is_connector:
                    # C6b: the latest, complete revision replaces the previous
                    # one. Only machine-only memories are retired (C9, inside
                    # retire_document_memories).
                    previous_id = head["published_document_id"] if head else None
                    if previous_id is not None and previous_id != doc_uuid:
                        from sourcemind.services.memory import retire as retire_module

                        await retire_module.retire_document_memories(
                            session,
                            workspace_id=ws_uuid,
                            document_id=previous_id,
                            reason="source_superseded",
                        )
                    if head is not None:
                        await _mark_published(session, head, doc_uuid)
                    extra["publication"] = "published"

                # Graph projection (flag-gated). One atomic commit follows, so these events
                # commit together with the memories, attributions and relations they describe.
                # Neighbours too: new relations change their importance and inbound edges.
                await emit_memory_states(
                    session,
                    [memory.id for memory in memories],
                    reason="ingest",
                    include_neighbours=True,
                )

                await update_document_status(
                    session,
                    doc_uuid,
                    IngestionStatus.COMPLETED,
                    memory_count=len(memories),
                    chunk_count=chunk_count,
                    current_stage="completed",
                )
                if extra:
                    await merge_pipeline_data(session, doc_uuid, extra)
                await session.commit()

                duration_ms = _elapsed_ms(start)
                log.info(
                    "pipeline_complete",
                    document_id=document_id,
                    memories_created=len(memories),
                    duration_ms=duration_ms,
                )
                return {
                    "status": "completed",
                    "document_id": document_id,
                    "memories_created": len(memories),
                    "duration_ms": duration_ms,
                }

            except Exception as exc:
                log.error(
                    "pipeline_attempt_failed",
                    document_id=document_id,
                    error_type=type(exc).__name__,
                )
                current_retries = int(getattr(getattr(task, "request", None), "retries", 0))
                max_retries = getattr(task, "max_retries", 3)
                will_retry = max_retries is None or current_retries < int(max_retries)
                try:
                    if attempt_savepoint.is_active:
                        await attempt_savepoint.rollback()
                    else:
                        await session.rollback()
                        recovery_result = await session.execute(
                            select(Document)
                            .where(*document_filters)
                            .with_for_update(key_share=True)
                            .execution_options(populate_existing=True)
                        )
                        recovered_doc = recovery_result.scalar_one_or_none()
                        if recovered_doc is None:
                            raise RuntimeError("Document disappeared during recovery")
                        if recovered_doc.ingestion_status == IngestionStatus.COMPLETED:
                            await session.commit()
                            return {
                                "status": "completed",
                                "document_id": document_id,
                                "memories_created": recovered_doc.memory_count,
                                "already_completed": True,
                            }
                        if recovered_doc.ingestion_status == IngestionStatus.FAILED:
                            await session.commit()
                            return {
                                "status": "failed",
                                "document_id": document_id,
                                "memories_created": recovered_doc.memory_count,
                                "already_terminal": True,
                                "error": recovered_doc.error_message,
                            }
                    await update_document_status(
                        session,
                        doc_uuid,
                        IngestionStatus.PROCESSING if will_retry else IngestionStatus.FAILED,
                        error_message=(
                            None if will_retry else f"Ingestion failed ({type(exc).__name__})."
                        ),
                        current_stage="retrying" if will_retry else "failed",
                    )
                    if not will_retry:
                        # Provider / infrastructure failure: eligible for the
                        # recovery sweep (connector documents).
                        await merge_pipeline_data(
                            session, doc_uuid, _failure_patch(base_pipeline_data, "transient")
                        )
                    await session.commit()
                except Exception as cleanup_exc:
                    # Failing to RECORD the failure is its own incident:
                    # the document is left mid-pipeline with no status.
                    log.error(
                        "ingestion.failure_status_update_failed",
                        document_id=str(doc_uuid),
                        error_type=type(cleanup_exc).__name__,
                    )

                if not will_retry:
                    raise RuntimeError("Ingestion pipeline failed.") from None

                log.warning(
                    "pipeline_retry_scheduled",
                    document_id=document_id,
                    retry=current_retries + 1,
                    max_retries=max_retries,
                )
                raise task.retry(  # type: ignore[union-attr]
                    exc=RuntimeError("Ingestion pipeline failed."),
                    countdown=60 * (2**current_retries),
                ) from None

    finally:
        await engine.dispose()
        await close_redis()


def _elapsed_ms(start: datetime) -> int:
    return int((datetime.now(UTC) - start).total_seconds() * 1000)


# ── Connector publication helpers (contract C6, C8, C10) ────────────────────

_RETRY_BACKOFF_BASE_SECONDS = 60
_RETRY_BACKOFF_MAX_SECONDS = 6 * 60 * 60


def _context_text(context: object) -> str | None:
    """Render contextual labels as plain text for the extraction prompt."""
    if not isinstance(context, dict) or not context:
        return None
    return "\n".join(f"{key}: {value}" for key, value in sorted(context.items()))


def _failure_patch(
    pipeline_data: dict,
    code: str,
    *,
    next_retry_at: datetime | None = None,
) -> dict:
    """pipeline_data fields recorded with ``ingestion_status = failed``.

    ``attempts`` is preserved (the recovery sweep increments it on requeue);
    ``next_retry_at`` is the earliest retry for codes that are retried.
    """
    attempts = int(pipeline_data.get("attempts", 0))
    if next_retry_at is None and code in {"transient", "incomplete_extraction"}:
        delay = min(_RETRY_BACKOFF_BASE_SECONDS * (2**attempts), _RETRY_BACKOFF_MAX_SECONDS)
        next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
    return {
        "error_code": code,
        "attempts": attempts,
        # None removes a stale value from an earlier failure.
        "next_retry_at": next_retry_at.astimezone(UTC).isoformat() if next_retry_at else None,
    }


_BLOCKED_STATES = frozenset({"disconnected", "paused"})


async def _connector_block_reason(
    session, doc, ws_uuid: uuid.UUID, *, keep_lock: bool
) -> str | None:
    """Why this document's connector may not be worked on right now, or ``None`` if it may.

    ``disconnected``: gone, invisible to this caller (fail closed), or re-created since the
    document was stamped (generation mismatch); permanent. ``paused``: paused by an admin,
    paused for the daily budget, or in error; the document is recoverable once the connector is
    active again, so it must not be reported as a disconnect.

    Read ``FOR SHARE``. With ``keep_lock=False`` the share lock is taken in a savepoint that is
    rolled back, so it is not held across provider calls.
    """
    from sqlalchemy import text

    statement = text(
        "SELECT status, generation FROM connector_configs "
        "WHERE id = CAST(:c AS uuid) AND workspace_id = CAST(:w AS uuid) FOR SHARE"
    )
    params = {"c": str(doc.connector_id), "w": str(ws_uuid)}
    if keep_lock:
        row = (await session.execute(statement, params)).first()
    else:
        nested = await session.begin_nested()
        try:
            row = (await session.execute(statement, params)).first()
        finally:
            await nested.rollback()
    if row is None or row.status == "disconnected":
        return "disconnected"
    if row.generation != getattr(doc, "connector_generation", None):
        return "disconnected"
    if row.status != "active":
        return "paused"
    return None


async def _lock_for_publication(session, doc, ws_uuid: uuid.UUID) -> tuple[str, dict | None]:
    """Take connector FOR SHARE then artifact_heads FOR UPDATE (C10 order).

    Returns ``(state, head)``; state is ``ok``, ``disconnected``,
    ``superseded`` (an older revision) or ``noop`` (this revision is already
    the published one). ``head`` is the locked row as a dict.
    """
    from sqlalchemy import text

    blocked = await _connector_block_reason(session, doc, ws_uuid, keep_lock=True)
    if blocked:
        return blocked, None
    link = (
        await session.execute(
            text(
                "SELECT revision_seq, source_tool, source_type, source_id FROM artifact_links "
                "WHERE document_id = CAST(:d AS uuid) ORDER BY created_at LIMIT 1"
            ),
            {"d": str(doc.id)},
        )
    ).first()
    if link is None:
        return "ok", None
    head_row = (
        await session.execute(
            text(
                "SELECT latest_seq, published_seq, published_document_id FROM artifact_heads "
                "WHERE workspace_id = CAST(:w AS uuid) AND source_tool = :t "
                "AND source_type = :k AND source_id = :s FOR UPDATE"
            ),
            {
                "w": str(ws_uuid),
                "t": link.source_tool,
                "k": link.source_type,
                "s": link.source_id,
            },
        )
    ).first()
    if head_row is None:
        if getattr(doc, "connector_id", None) is not None:
            # The receiver always creates the head of a connector artifact, so a missing row means
            # row-level security hid it (the submitter is no longer an owner/admin of the
            # workspace) or it was removed. Publishing without the head lock would skip the
            # supersession rules, so fail closed; the task is retried and ends failed.
            raise RuntimeError("artifact head is not visible to the publishing principal")
        return "ok", None
    head = {
        "latest_seq": int(head_row.latest_seq),
        "published_seq": int(head_row.published_seq),
        "published_document_id": head_row.published_document_id,
        "my_seq": int(link.revision_seq),
        "workspace_id": ws_uuid,
        "source_tool": link.source_tool,
        "source_type": link.source_type,
        "source_id": link.source_id,
    }
    mine = head["my_seq"]
    if head["published_document_id"] == doc.id and head["published_seq"] == mine:
        return "noop", head
    if mine < head["latest_seq"] or mine <= head["published_seq"]:
        return "superseded", head
    return "ok", head


async def _mark_published(session, head: dict, doc_uuid: uuid.UUID) -> None:
    from sqlalchemy import text

    await session.execute(
        text(
            "UPDATE artifact_heads SET published_seq = :n, "
            "published_document_id = CAST(:d AS uuid), updated_at = now() "
            "WHERE workspace_id = CAST(:w AS uuid) AND source_tool = :t "
            "AND source_type = :k AND source_id = :s"
        ),
        {
            "n": head["my_seq"],
            "d": str(doc_uuid),
            "w": str(head["workspace_id"]),
            "t": head["source_tool"],
            "k": head["source_type"],
            "s": head["source_id"],
        },
    )


async def _fail_disconnected(
    session, doc_uuid: uuid.UUID, document_id: str, start, *, reason: str = "disconnected"
) -> dict:
    """C8: no provider call, no publication; the document fails and is committed.

    ``reason`` is ``disconnected`` (permanent) or ``paused`` (recoverable: ``retry-failed`` after
    the connector is active again). The error code says which; a pause is not a disconnect.
    """
    code = "connector_paused" if reason == "paused" else "connector_disconnected"
    text_ = "connector paused" if reason == "paused" else "connector disconnected"
    from sourcemind.models.document import IngestionStatus
    from sourcemind.services.memory import store

    log.warning("pipeline_connector_blocked", document_id=document_id, reason=reason)
    await store.update_document_status(
        session,
        doc_uuid,
        IngestionStatus.FAILED,
        memory_count=0,
        error_message=text_,
        current_stage="failed",
    )
    await store.merge_pipeline_data(
        session,
        doc_uuid,
        {"error_code": code, "next_retry_at": None},
    )
    await session.commit()
    return {
        "status": "failed",
        "document_id": document_id,
        "memories_created": 0,
        "error": text_,
        "error_code": code,
        "duration_ms": _elapsed_ms(start),
    }


def _next_utc_midnight(now: datetime) -> datetime:
    day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return day + timedelta(days=1)


# One extraction attempt plus the single retry the extractor makes per chunk.
MAX_CALLS_PER_CHUNK = 2


async def _refund_budget(ws_uuid: uuid.UUID, calls: int) -> None:
    """Return reserved-but-unused calls to today's counter (never below zero)."""
    if calls <= 0:
        return
    from sourcemind.core.redis_client import get_redis

    now = datetime.now(UTC)
    await get_redis().decrby(f"extraction_budget:{ws_uuid}:{now:%Y%m%d}", calls)


async def _reserve_budget(ws_uuid: uuid.UUID, calls: int, limit: int) -> bool:
    """Add ``calls`` to the workspace's UTC-day counter; refund and refuse if over."""
    from sourcemind.core.redis_client import get_redis

    redis = get_redis()
    now = datetime.now(UTC)
    key = f"extraction_budget:{ws_uuid}:{now:%Y%m%d}"
    total = int(await redis.incrby(key, calls))
    await redis.expire(key, 2 * 24 * 3600)
    if total > limit:
        await redis.decrby(key, calls)
        return False
    return True


async def _fail_budget(
    session, doc, doc_uuid: uuid.UUID, document_id: str, ws_uuid: uuid.UUID, extra: dict, start
) -> dict:
    """Daily budget reached: fail the document and pause the connector."""
    from sqlalchemy import text

    from sourcemind.models.document import IngestionStatus
    from sourcemind.services.memory import store

    log.warning("pipeline_budget_exhausted", document_id=document_id, workspace_id=str(ws_uuid))
    await store.update_document_status(
        session,
        doc_uuid,
        IngestionStatus.FAILED,
        memory_count=0,
        error_message="daily extraction budget reached",
        current_stage="failed",
    )
    patch = _failure_patch(
        dict(doc.pipeline_data or {}),
        "budget",
        next_retry_at=_next_utc_midnight(datetime.now(UTC)),
    )
    await store.merge_pipeline_data(session, doc_uuid, {**extra, **patch})
    # Update under the connector row lock (C10: before any document-level work
    # that depends on it; no memory rows are held here).
    await session.execute(
        text(
            "SELECT 1 FROM connector_configs WHERE id = CAST(:c AS uuid) "
            "AND workspace_id = CAST(:w AS uuid) FOR UPDATE"
        ),
        {"c": str(doc.connector_id), "w": str(ws_uuid)},
    )
    await session.execute(
        text(
            "UPDATE connector_configs SET status = 'paused_budget' "
            "WHERE id = CAST(:c AS uuid) AND workspace_id = CAST(:w AS uuid) "
            "AND status = 'active'"
        ),
        {"c": str(doc.connector_id), "w": str(ws_uuid)},
    )
    await session.commit()
    return {
        "status": "failed",
        "document_id": document_id,
        "memories_created": 0,
        "error": "daily extraction budget reached",
        "error_code": "budget",
        "duration_ms": _elapsed_ms(start),
    }
