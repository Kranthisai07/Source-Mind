"""
Memory versioning — creates a new version on every PATCH.

Every content edit creates a new Memory row (immutable append):
  1. Set current_version=False on the existing memory
  2. INSERT new memory: version+1, parent_memory_id=old.id, current_version=True
  3. Recompute embedding if content changed
  4. Copy memory_relations from old to new

GET /v1/memories/:id/versions uses a recursive CTE to walk the version chain.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.models.memory import Memory
from sourcemind.services.ingestion.embedder import EmbeddingService

log = structlog.get_logger(__name__)


@dataclass
class VersionResult:
    """Result from create_new_version()."""
    new_memory: Memory
    previous_version_id: uuid.UUID
    embedding_recomputed: bool


async def create_new_version(
    session: AsyncSession,
    memory_id: uuid.UUID,
    new_content: str,
    new_tags: list[str] | None,
    openai_client: object | None = None,
) -> VersionResult:
    """
    Create a new version of a memory with updated content.

    Steps:
      1. Load the current version (raises if not found)
      2. Set current_version=False on old record
      3. Recompute embedding if content changed
      4. INSERT new memory record (version+1)
      5. Copy memory_relations from old to new

    Returns VersionResult with the new Memory ORM object.
    """
    from sqlalchemy import select

    # 1. Load current version
    result = await session.execute(
        select(Memory).where(
            Memory.id == memory_id,
            Memory.current_version.is_(True),
            Memory.deleted_at.is_(None),
        )
    )
    old = result.scalar_one_or_none()
    if not old:
        from sourcemind.core.exceptions import MemoryNotFoundError
        raise MemoryNotFoundError(f"Memory {memory_id} not found or not current version.")

    content_changed = old.content != new_content

    # 2. Mark old version as non-current
    await session.execute(
        text("UPDATE memories SET current_version = FALSE WHERE id = CAST(:id AS uuid)"),
        {"id": str(old.id)},
    )

    # 3. Recompute embedding if content changed
    new_embedding = old.embedding
    embedding_recomputed = False
    new_content_hash = hashlib.sha256(new_content.encode()).hexdigest()

    if content_changed and openai_client is not None:
        try:
            embedder = EmbeddingService(openai_client)
            results = await embedder.embed([new_content])
            if results:
                new_embedding = results[0].embedding
                embedding_recomputed = True
        except Exception as exc:
            log.warning("versioning_embed_failed", error=str(exc))
            new_embedding = None  # Will be recomputed async later

    # 4. INSERT new memory row
    new_memory = Memory(
        workspace_id=old.workspace_id,
        document_id=old.document_id,
        parent_memory_id=old.id,
        content=new_content,
        embedding=new_embedding,
        content_hash=new_content_hash,
        version=old.version + 1,
        current_version=True,
        tags=new_tags if new_tags is not None else old.tags,
        category=old.category,
        source_chunk_index=old.source_chunk_index,
        confidence_score=old.confidence_score,
    )
    session.add(new_memory)
    await session.flush()  # get new_memory.id

    # 5. Copy memory_relations (both directions)
    await session.execute(
        text("""
            INSERT INTO memory_relations (
                source_memory_id, target_memory_id, relation_type,
                confidence, similarity_score, detected_by
            )
            SELECT CAST(:new_id AS uuid), target_memory_id, relation_type,
                   confidence, similarity_score, detected_by
            FROM memory_relations
            WHERE source_memory_id = CAST(:old_id AS uuid)
            ON CONFLICT DO NOTHING
        """),
        {"new_id": str(new_memory.id), "old_id": str(old.id)},
    )
    await session.execute(
        text("""
            INSERT INTO memory_relations (
                source_memory_id, target_memory_id, relation_type,
                confidence, similarity_score, detected_by
            )
            SELECT source_memory_id, CAST(:new_id AS uuid), relation_type,
                   confidence, similarity_score, detected_by
            FROM memory_relations
            WHERE target_memory_id = CAST(:old_id AS uuid)
            ON CONFLICT DO NOTHING
        """),
        {"new_id": str(new_memory.id), "old_id": str(old.id)},
    )

    log.info(
        "memory_version_created",
        old_id=str(old.id),
        new_id=str(new_memory.id),
        version=new_memory.version,
        embedding_recomputed=embedding_recomputed,
    )

    return VersionResult(
        new_memory=new_memory,
        previous_version_id=old.id,
        embedding_recomputed=embedding_recomputed,
    )


async def get_version_chain(
    session: AsyncSession,
    memory_id: uuid.UUID,
) -> list[dict]:
    """
    Return the full version chain for a memory using a recursive CTE.

    Walks backwards through parent_memory_id links.
    Returns ordered list from latest to oldest (version DESC).
    """
    result = await session.execute(
        text("""
            WITH RECURSIVE version_chain AS (
                SELECT id, workspace_id, content, version, current_version,
                       parent_memory_id, created_at, deleted_at
                FROM memories
                WHERE id = CAST(:memory_id AS uuid)

                UNION ALL

                SELECT m.id, m.workspace_id, m.content, m.version, m.current_version,
                       m.parent_memory_id, m.created_at, m.deleted_at
                FROM memories m
                JOIN version_chain vc ON m.id = vc.parent_memory_id
            )
            SELECT id::text, version, current_version, content, created_at
            FROM version_chain
            ORDER BY version DESC
        """),
        {"memory_id": str(memory_id)},
    )
    rows = result.fetchall()
    return [
        {
            "id": row[0],
            "version": row[1],
            "is_current": row[2],
            "content": row[3],
            "created_at": row[4],
        }
        for row in rows
    ]
