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
from datetime import datetime, timezone

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
    from sourcemind.core.redis_client import close_redis, init_redis
    from sourcemind.models.document import Document, IngestionStatus
    from sourcemind.services.attribution.engine import create_initial_attribution
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
    start = datetime.now(timezone.utc)

    try:
        async with factory() as session:
            result = await session.execute(select(Document).where(Document.id == doc_uuid))
            doc = result.scalar_one_or_none()
            if not doc:
                log.error("pipeline_doc_not_found", document_id=document_id)
                return {"status": "failed", "error": "Document not found"}

            try:
                # Stage 2: EXTRACT
                await update_document_status(
                    session, doc_uuid, IngestionStatus.PROCESSING, current_stage="extracting"
                )
                raw_content = (doc.pipeline_data or {}).get("raw_content")
                extraction = await extract(
                    content=raw_content,
                    url=doc.source_url if doc.source_type == "url" else None,
                    source_type=doc.source_type,
                )

                # Stage 3: CHUNK
                await update_document_status(
                    session, doc_uuid, IngestionStatus.PROCESSING, current_stage="chunking"
                )
                chunks = await chunk(extraction)
                log.info("pipeline_chunked", document_id=document_id, chunks=len(chunks))

                # Stage 4: FACT EXTRACTION
                await update_document_status(
                    session, doc_uuid, IngestionStatus.PROCESSING, current_stage="extracting_facts"
                )
                facts = await fact_extractor.extract(
                    chunks,
                    source_url=doc.source_url,
                    content_type=doc.source_type,
                )
                log.info("pipeline_facts", document_id=document_id, facts=len(facts))

                if not facts:
                    log.warning("pipeline_no_facts", document_id=document_id)
                    await update_document_status(
                        session,
                        doc_uuid,
                        IngestionStatus.COMPLETED,
                        memory_count=0,
                        chunk_count=len(chunks),
                        current_stage="completed",
                    )
                    await session.commit()
                    return {
                        "status": "completed",
                        "document_id": document_id,
                        "memories_created": 0,
                        "duration_ms": _elapsed_ms(start),
                    }

                # Stage 5: EMBED
                await update_document_status(
                    session, doc_uuid, IngestionStatus.PROCESSING, current_stage="embedding"
                )
                embedding_results = await embedder.embed(facts)

                # Stages 6+7: ATTRIBUTE + INDEX
                await update_document_status(
                    session, doc_uuid, IngestionStatus.PROCESSING, current_stage="indexing"
                )
                memories = await store_memories(session, ws_uuid, doc_uuid, embedding_results, {})

                for memory in memories:
                    await create_initial_attribution(
                        session, memory.id, user_uuid, memory.content, doc.source_type
                    )

                # Connector-sourced documents have a pending ArtifactLink whose
                # memory_id could not be set at sync time — fill it in now.
                await backfill_artifact_links(session, doc_uuid, memories)

                await relation_detector.detect(session, memories, ws_uuid)

                await update_document_status(
                    session,
                    doc_uuid,
                    IngestionStatus.COMPLETED,
                    memory_count=len(memories),
                    chunk_count=len(chunks),
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
                    "pipeline_failed", document_id=document_id, error=str(exc), exc_info=True
                )
                try:
                    await session.rollback()
                    await update_document_status(
                        session,
                        doc_uuid,
                        IngestionStatus.FAILED,
                        error_message=str(exc)[:500],
                        current_stage="failed",
                    )
                    await session.commit()
                except Exception:
                    pass

                raise task.retry(exc=exc, countdown=60 * (2 ** task.request.retries))  # type: ignore[union-attr]

    finally:
        await engine.dispose()
        await close_redis()


def _elapsed_ms(start: datetime) -> int:
    return int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
