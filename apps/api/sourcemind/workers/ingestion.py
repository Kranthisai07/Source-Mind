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
from datetime import UTC, datetime

import structlog

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
                .with_for_update(skip_locked=True)
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

            attempt_savepoint = await session.begin_nested()
            try:
                pipeline_data = doc.pipeline_data or {}
                raw_content = pipeline_data.get("raw_content")
                verbatim = pipeline_data.get("ingestion_mode") == "verbatim"
                if verbatim:
                    if not isinstance(raw_content, str) or not raw_content.strip():
                        raise ValueError("verbatim ingestion requires non-empty raw_content")
                    facts = [raw_content]
                    chunk_count = 1
                    fact_extraction = None
                    log.info("pipeline_verbatim", document_id=document_id)
                else:
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
                    chunk_count = len(chunks)
                    log.info("pipeline_chunked", document_id=document_id, chunks=chunk_count)

                    # Stage 4: FACT EXTRACTION
                    await update_document_status(
                        session,
                        doc_uuid,
                        IngestionStatus.PROCESSING,
                        current_stage="extracting_facts",
                    )
                    fact_extraction = await fact_extractor.extract(
                        chunks,
                        source_url=doc.source_url,
                        content_type=doc.source_type,
                    )
                    facts = fact_extraction.facts
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
                if fact_extraction is not None and fact_extraction.wholly_failed:
                    detail = "; ".join(fact_extraction.failure_reasons)[:480]
                    log.error(
                        "pipeline_extraction_failed",
                        document_id=document_id,
                        failed_chunks=fact_extraction.failed_chunks,
                        total_chunks=fact_extraction.total_chunks,
                        detail=detail,
                    )
                    await update_document_status(
                        session,
                        doc_uuid,
                        IngestionStatus.FAILED,
                        memory_count=0,
                        chunk_count=chunk_count,
                        error_message=f"fact extraction failed: {detail}",
                        current_stage="failed",
                    )
                    await session.commit()
                    return {
                        "status": "failed",
                        "document_id": document_id,
                        "memories_created": 0,
                        "error": "fact extraction failed",
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
                    await update_document_status(
                        session,
                        doc_uuid,
                        IngestionStatus.COMPLETED,
                        memory_count=0,
                        chunk_count=chunk_count,
                        current_stage="completed",
                    )
                    await session.commit()
                    return {
                        "status": "completed",
                        "document_id": document_id,
                        "memories_created": 0,
                        "duration_ms": _elapsed_ms(start),
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

                await update_document_status(
                    session,
                    doc_uuid,
                    IngestionStatus.COMPLETED,
                    memory_count=len(memories),
                    chunk_count=chunk_count,
                    current_stage="completed",
                )
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
                            .with_for_update()
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
