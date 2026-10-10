"""Read-only workspace export for the bounded manual pilot."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_DOCUMENTS = """
SELECT
    id::text AS id,
    workspace_id::text AS workspace_id,
    submitter_id::text AS submitter_id,
    title,
    source_type,
    source_url,
    sha256_hash,
    ingestion_status,
    ingestion_job_id,
    error_message,
    chunk_count,
    memory_count,
    pipeline_data,
    created_at,
    updated_at,
    deleted_at
FROM documents
WHERE workspace_id = CAST(:workspace_id AS uuid)
ORDER BY created_at, id
"""

_MEMORIES = """
SELECT
    id::text AS id,
    workspace_id::text AS workspace_id,
    document_id::text AS document_id,
    parent_memory_id::text AS parent_memory_id,
    content,
    content_hash,
    version,
    current_version,
    tags,
    category,
    confidence_score,
    source_chunk_index,
    importance_score,
    created_at,
    updated_at,
    deleted_at
FROM memories
WHERE workspace_id = CAST(:workspace_id AS uuid)
  AND current_version = TRUE
ORDER BY created_at, id
"""

_VERSIONS = """
SELECT
    id::text AS id,
    workspace_id::text AS workspace_id,
    document_id::text AS document_id,
    parent_memory_id::text AS parent_memory_id,
    content,
    content_hash,
    version,
    current_version,
    tags,
    category,
    confidence_score,
    source_chunk_index,
    importance_score,
    created_at,
    updated_at,
    deleted_at
FROM memories
WHERE workspace_id = CAST(:workspace_id AS uuid)
ORDER BY COALESCE(parent_memory_id, id), version, created_at, id
"""

_ATTRIBUTIONS = """
SELECT
    a.id::text AS id,
    a.memory_id::text AS memory_id,
    a.user_id::text AS user_id,
    a.contribution_weight,
    a.char_diff_score,
    a.semantic_score,
    a.temporal_score,
    a.structural_score,
    a.approval_score,
    a.trigger_action,
    a.edit_id::text AS edit_id,
    a.created_at
FROM attributions AS a
JOIN memories AS m ON m.id = a.memory_id
WHERE m.workspace_id = CAST(:workspace_id AS uuid)
ORDER BY a.created_at, a.id
"""

_ATTRIBUTION_EDITS = """
SELECT
    e.id::text AS id,
    e.memory_id::text AS memory_id,
    e.editor_id::text AS editor_id,
    e.content_before,
    e.content_after,
    e.edit_position,
    e.action_type,
    e.idempotency_key,
    e.created_at,
    e.updated_at
FROM attribution_edits AS e
JOIN memories AS m ON m.id = e.memory_id
WHERE m.workspace_id = CAST(:workspace_id AS uuid)
ORDER BY e.created_at, e.id
"""


async def _rows(
    session: AsyncSession,
    statement: str,
    workspace_id: uuid.UUID,
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(statement),
        {"workspace_id": str(workspace_id)},
    )
    return [dict(row) for row in result.mappings().all()]


async def export_workspace_pilot_data(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> dict[str, Any]:
    """Export only rows explicitly joined or filtered to ``workspace_id``."""
    return {
        "export_schema_version": 1,
        "exported_at": datetime.now(UTC),
        "workspace_id": str(workspace_id),
        "documents": await _rows(session, _DOCUMENTS, workspace_id),
        "memories": await _rows(session, _MEMORIES, workspace_id),
        "versions": await _rows(session, _VERSIONS, workspace_id),
        "attributions": await _rows(session, _ATTRIBUTIONS, workspace_id),
        "attribution_edits": await _rows(session, _ATTRIBUTION_EDITS, workspace_id),
        "search_events": [],
        "search_ratings": [],
        "unavailable_datasets": {
            "search_events": (
                "No search_events table exists on this base; add the proposed pilot "
                "search-evidence migration before collecting search interactions."
            ),
            "search_ratings": (
                "No search_ratings table exists on this base; add the proposed pilot "
                "search-evidence migration before collecting ratings."
            ),
        },
    }
