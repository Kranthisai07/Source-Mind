"""
Stage 7a — INDEX: Write memories and embeddings to PostgreSQL.

Inserts one Memory row per EmbeddingResult.
Updates Document.ingestion_status and Document.memory_count on completion.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.models.document import Document, IngestionStatus
from sourcemind.models.memory import Memory

if TYPE_CHECKING:
    from sourcemind.services.ingestion.embedder import EmbeddingResult

log = structlog.get_logger(__name__)


async def store_memories(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    embedding_results: list[EmbeddingResult],
    source_metadata: dict[str, Any],
) -> list[Memory]:
    """
    Insert Memory rows for all EmbeddingResults.

    Each memory is version=1, current_version=True.
    Flushes after each insert to get the generated UUID.
    Returns all created Memory objects.
    """
    memories: list[Memory] = []

    for i, result in enumerate(embedding_results):
        content_hash = hashlib.sha256(result.content.encode()).hexdigest()

        memory = Memory(
            workspace_id=workspace_id,
            document_id=document_id,
            content=result.content,
            embedding=result.embedding,
            content_hash=content_hash,
            version=1,
            current_version=True,
            source_chunk_index=i,
            confidence_score=0.9,
        )
        session.add(memory)
        memories.append(memory)

    # Flush to generate UUIDs for all memories
    await session.flush()

    log.info(
        "memories_stored",
        count=len(memories),
        document_id=str(document_id),
        workspace_id=str(workspace_id),
    )
    return memories


async def backfill_artifact_links(
    session: AsyncSession,
    document_id: uuid.UUID,
    memories: list[Memory],
) -> int:
    """Point pending ArtifactLink rows at the memories the document produced.

    Connectors create an ArtifactLink at sync time with ``document_id`` set and
    ``memory_id`` NULL, because memories do not exist until this pipeline runs.

    A document normally yields several memories, so we create **one link row
    per memory**: the first memory claims the existing pending row, and each
    remaining memory gets a clone of it. That lets every memory be traced back
    to its originating artifact independently.

    No-ops in two cases, both expected:

      * The document did not come from a connector (the common case for a
        direct POST /v1/memories) — no pending row exists.
      * The document produced no facts, so no memories exist to link to.
        Its ArtifactLink stays permanently at memory_id NULL, which is the
        honest representation: the artifact was ingested and yielded nothing.
        Dedup still works, since _ingest matches on source_id.

    Returns:
        The number of link rows now pointing at a memory.
    """
    if not memories:
        return 0

    pending = (
        await session.execute(
            text(
                "SELECT id FROM artifact_links "
                "WHERE document_id = CAST(:doc AS uuid) AND memory_id IS NULL "
                "ORDER BY created_at LIMIT 1"
            ),
            {"doc": str(document_id)},
        )
    ).first()

    if pending is None:
        return 0

    anchor_id = str(pending.id)

    # First memory claims the anchor row.
    await session.execute(
        text(
            "UPDATE artifact_links SET memory_id = CAST(:mid AS uuid) "
            "WHERE id = CAST(:aid AS uuid)"
        ),
        {"mid": str(memories[0].id), "aid": anchor_id},
    )

    # Remaining memories get clones of it. `metadata` is the real column name
    # (artifact_metadata is only the ORM attribute).
    for memory in memories[1:]:
        await session.execute(
            text(
                "INSERT INTO artifact_links ("
                "  workspace_id, document_id, memory_id, source_tool, "
                "  source_type, source_id, source_url, source_author, "
                "  source_timestamp, resolved_user_id, identity_confidence, "
                "  metadata"
                ") SELECT "
                "  workspace_id, document_id, CAST(:mid AS uuid), source_tool, "
                "  source_type, source_id, source_url, source_author, "
                "  source_timestamp, resolved_user_id, identity_confidence, "
                "  metadata "
                "FROM artifact_links WHERE id = CAST(:aid AS uuid) "
                "ON CONFLICT DO NOTHING"
            ),
            {"mid": str(memory.id), "aid": anchor_id},
        )

    await session.flush()
    log.info(
        "artifact_links_backfilled",
        document_id=str(document_id),
        links=len(memories),
    )
    return len(memories)


async def update_document_status(
    session: AsyncSession,
    document_id: uuid.UUID,
    status: IngestionStatus,
    memory_count: int | None = None,
    chunk_count: int | None = None,
    error_message: str | None = None,
    current_stage: str | None = None,
) -> None:
    """Update document processing status and optional counters."""
    updates: list[str] = ["ingestion_status = :status"]
    params: dict[str, Any] = {"status": status.value, "doc_id": str(document_id)}

    if memory_count is not None:
        updates.append("memory_count = :memory_count")
        params["memory_count"] = memory_count
    if chunk_count is not None:
        updates.append("chunk_count = :chunk_count")
        params["chunk_count"] = chunk_count
    if error_message is not None:
        updates.append("error_message = :error_message")
        params["error_message"] = error_message

    # Store fine-grained stage in the pipeline JSONB column. The DB column is
    # `metadata`; `pipeline_data` is only the ORM attribute name (see
    # models/document.py), so raw SQL must use the real column name.
    if current_stage is not None:
        updates.append(
            "metadata = jsonb_set(COALESCE(metadata, '{}'), "
            "'{current_stage}', to_jsonb(CAST(:current_stage AS text)))"
        )
        params["current_stage"] = current_stage

    sql = f"UPDATE documents SET {', '.join(updates)} WHERE id = CAST(:doc_id AS uuid)"
    await session.execute(text(sql), params)
    await session.flush()
