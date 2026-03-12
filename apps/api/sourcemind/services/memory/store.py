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

    # Store fine-grained stage in pipeline_data JSONB
    if current_stage is not None:
        updates.append(
            "pipeline_data = jsonb_set(COALESCE(pipeline_data, '{}'), "
            "'{current_stage}', to_jsonb(:current_stage::text))"
        )
        params["current_stage"] = current_stage

    sql = f"UPDATE documents SET {', '.join(updates)} WHERE id = :doc_id::uuid"
    await session.execute(text(sql), params)
    await session.flush()
