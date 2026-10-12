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
from enum import StrEnum

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.dependencies import WorkspacePermission, require_memory_access
from sourcemind.core.exceptions import (
    MemoryNotFoundError,
    MemoryVersionStaleError,
    SourceMindError,
)
from sourcemind.models.memory import Memory
from sourcemind.services.attribution.engine import MAX_CHAIN_DEPTH
from sourcemind.services.ingestion.embedder import EmbeddingService

log = structlog.get_logger(__name__)


@dataclass
class VersionResult:
    """Result from create_new_version()."""
    new_memory: Memory
    previous_version_id: uuid.UUID
    embedding_recomputed: bool


class SupersessionReason(StrEnum):
    """Why a non-current memory version cannot be edited (D-025)."""

    SUPERSEDED = "superseded"
    AMBIGUOUS_SUCCESSOR = "ambiguous_successor"
    NO_CURRENT_VERSION = "no_current_version"
    SUPERSEDED_WITHOUT_SUCCESSOR = "superseded_without_successor"


@dataclass(frozen=True)
class LatestVersion:
    """Outcome of find_latest_version().

    reason is None when the memory itself is current. latest_memory_id is set
    ONLY when reason is SUPERSEDED: exactly one current, non-deleted descendant
    that the caller is authorized to read.
    """

    reason: SupersessionReason | None
    latest_memory_id: uuid.UUID | None = None


async def find_latest_version(
    session: AsyncSession,
    memory_id: uuid.UUID,
    workspace_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None,
) -> LatestVersion:
    """
    Identify the single authorized current descendant of a memory version.

    Walks DOWNWARD through parent_memory_id links (bounded by MAX_CHAIN_DEPTH,
    cycle-guarded, same workspace only) and never chooses between candidates:

      exactly one current non-deleted descendant -> SUPERSEDED + its id
      more than one (a fork)                     -> AMBIGUOUS_SUCCESSOR, no id
      descendants, none current and non-deleted  -> NO_CURRENT_VERSION, no id
      no descendants, row itself non-current     -> SUPERSEDED_WITHOUT_SUCCESSOR
                                                    (retired by conflict/merge)

    A unique id is only returned after the caller's READ access to THAT id is
    confirmed with the same helper used for memories (user_id None counts as
    unverifiable); if it fails the result is downgraded to
    SUPERSEDED_WITHOUT_SUCCESSOR so no id leaks.
    """
    own = (
        await session.execute(
            text(
                "SELECT current_version FROM memories "
                "WHERE id = CAST(:mid AS uuid) AND workspace_id = CAST(:ws AS uuid)"
            ),
            {"mid": str(memory_id), "ws": str(workspace_id)},
        )
    ).first()
    if own is not None and own.current_version:
        return LatestVersion(reason=None)

    summary = (
        await session.execute(
            text("""
                WITH RECURSIVE descendants AS (
                    SELECT m.id, m.current_version, m.deleted_at, 1 AS depth,
                           ARRAY[CAST(:mid AS uuid), m.id] AS path
                    FROM memories m
                    WHERE m.parent_memory_id = CAST(:mid AS uuid)
                      AND m.workspace_id = CAST(:ws AS uuid)
                    UNION ALL
                    SELECT m.id, m.current_version, m.deleted_at, d.depth + 1,
                           d.path || m.id
                    FROM memories m
                    JOIN descendants d ON m.parent_memory_id = d.id
                    WHERE m.workspace_id = CAST(:ws AS uuid)
                      AND d.depth < :bound
                      AND NOT m.id = ANY(d.path)
                )
                SELECT count(*) AS total,
                       count(*) FILTER (
                           WHERE current_version AND deleted_at IS NULL
                       ) AS live,
                       min(id::text) FILTER (
                           WHERE current_version AND deleted_at IS NULL
                       ) AS only_id
                FROM descendants
            """),
            {"mid": str(memory_id), "ws": str(workspace_id), "bound": MAX_CHAIN_DEPTH},
        )
    ).one()

    if summary.total == 0:
        return LatestVersion(SupersessionReason.SUPERSEDED_WITHOUT_SUCCESSOR)
    if summary.live == 0:
        return LatestVersion(SupersessionReason.NO_CURRENT_VERSION)
    if summary.live > 1:
        return LatestVersion(SupersessionReason.AMBIGUOUS_SUCCESSOR)

    latest_id = uuid.UUID(summary.only_id)
    if user_id is None:
        # Authorization of the successor cannot be checked; never leak its id.
        return LatestVersion(SupersessionReason.SUPERSEDED_WITHOUT_SUCCESSOR)
    try:
        await require_memory_access(
            session, user_id, latest_id, WorkspacePermission.READ
        )
    except SourceMindError:
        log.warning("superseded_successor_not_authorized", memory_id=str(memory_id))
        return LatestVersion(SupersessionReason.SUPERSEDED_WITHOUT_SUCCESSOR)
    return LatestVersion(SupersessionReason.SUPERSEDED, latest_id)


async def unavailable_version_error(
    session: AsyncSession,
    memory_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None,
) -> SourceMindError:
    """
    Build the error for a memory that is not the editable current version.

    MUST be called only after the caller's CONTRIBUTE access to memory_id has
    passed. A row that exists, is not deleted and is not current yields the
    409 SM035 with details {memory_id, latest_memory_id, reason}; anything else
    (missing or deleted) keeps the generic 404 SM020. Nothing is created and
    nothing is applied to the latest version: a retried PATCH never "succeeds
    by replay" - successful idempotent replay is deliberately NOT implemented
    (D-025).
    """
    row = (
        await session.execute(
            text(
                "SELECT workspace_id, current_version, deleted_at FROM memories "
                "WHERE id = CAST(:mid AS uuid)"
            ),
            {"mid": str(memory_id)},
        )
    ).first()
    if row is None or row.deleted_at is not None or row.current_version:
        return MemoryNotFoundError(f"Memory {memory_id} not found or not current version.")

    latest = await find_latest_version(
        session, memory_id, row.workspace_id, user_id=user_id
    )
    return MemoryVersionStaleError(
        f"Memory {memory_id} has been superseded and cannot be edited.",
        details={
            "memory_id": str(memory_id),
            "latest_memory_id": (
                str(latest.latest_memory_id) if latest.latest_memory_id else None
            ),
            "reason": latest.reason.value,
        },
    )


async def create_new_version(
    session: AsyncSession,
    memory_id: uuid.UUID,
    new_content: str,
    new_tags: list[str] | None,
    openai_client: object | None = None,
    user_id: uuid.UUID | None = None,
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
        # Serialise concurrent edits of the same version: a second writer
        # blocks, then re-evaluates current_version and finds none instead
        # of forking the chain.
        .with_for_update()
    )
    old = result.scalar_one_or_none()
    if not old:
        raise await unavailable_version_error(session, memory_id, user_id=user_id)

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
