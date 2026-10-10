"""Read-only workspace export for the bounded manual pilot."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import orjson
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
    metadata AS pipeline_data,
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

_RELATIONS = """
SELECT
    r.id::text AS id,
    source.workspace_id::text AS workspace_id,
    r.source_memory_id::text AS source_memory_id,
    r.target_memory_id::text AS target_memory_id,
    r.relation_type,
    r.similarity_score,
    r.confidence,
    r.detected_by,
    r.created_at,
    r.updated_at
FROM memory_relations AS r
JOIN memories AS source ON source.id = r.source_memory_id
JOIN memories AS target ON target.id = r.target_memory_id
WHERE source.workspace_id = CAST(:workspace_id AS uuid)
  AND target.workspace_id = CAST(:workspace_id AS uuid)
ORDER BY r.created_at, r.id
"""

_CONFLICTS = """
SELECT
    id::text AS id,
    workspace_id::text AS workspace_id,
    memory_a_id::text AS memory_a_id,
    memory_b_id::text AS memory_b_id,
    conflict_type,
    severity,
    competing_claim_count,
    blocks_derivation,
    status,
    similarity_score,
    explanation,
    resolver_id::text AS resolver_id,
    resolved_at,
    resolution_note,
    created_at,
    updated_at
FROM memory_conflicts
WHERE workspace_id = CAST(:workspace_id AS uuid)
ORDER BY created_at, id
"""

_WORKSPACE_MEMBERS = """
SELECT
    id::text AS id,
    workspace_id::text AS workspace_id,
    user_id::text AS user_id,
    role,
    status,
    departed_at,
    invited_by_id::text AS invited_by_id,
    created_at,
    updated_at
FROM workspace_members
WHERE workspace_id = CAST(:workspace_id AS uuid)
ORDER BY created_at, id
"""

_SEARCH_EVENTS = """
SELECT
    id::text AS id,
    workspace_id::text AS workspace_id,
    requester_user_id::text AS requester_user_id,
    query,
    search_parameters,
    request_id,
    result_snapshot,
    memory_ids,
    document_ids,
    algorithm_id,
    created_at
FROM search_events
WHERE workspace_id = CAST(:workspace_id AS uuid)
ORDER BY created_at, id
"""

_SEARCH_RATINGS = """
SELECT
    id::text AS id,
    workspace_id::text AS workspace_id,
    search_event_id::text AS search_event_id,
    memory_id::text AS memory_id,
    result_rank,
    rater_user_id::text AS rater_user_id,
    rater_pseudonym,
    rating_source,
    allocation,
    exclusion_reason,
    idempotency_key::text AS idempotency_key,
    recorded_by_user_id::text AS recorded_by_user_id,
    created_at
FROM search_ratings
WHERE workspace_id = CAST(:workspace_id AS uuid)
ORDER BY created_at, id
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


def _canonical_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"Unsupported canonical export value: {type(value).__name__}")


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = orjson.dumps(
        payload,
        default=_canonical_default,
        option=orjson.OPT_SORT_KEYS,
    )
    return hashlib.sha256(encoded).hexdigest()


async def export_workspace_pilot_data(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> dict[str, Any]:
    """Export only rows explicitly joined or filtered to ``workspace_id``."""
    canonical_payload = {
        "export_schema_version": 2,
        "workspace_id": str(workspace_id),
        "documents": await _rows(session, _DOCUMENTS, workspace_id),
        "memories": await _rows(session, _MEMORIES, workspace_id),
        "versions": await _rows(session, _VERSIONS, workspace_id),
        "attributions": await _rows(session, _ATTRIBUTIONS, workspace_id),
        "attribution_edits": await _rows(session, _ATTRIBUTION_EDITS, workspace_id),
        "relations": await _rows(session, _RELATIONS, workspace_id),
        "conflicts": await _rows(session, _CONFLICTS, workspace_id),
        "workspace_members": await _rows(session, _WORKSPACE_MEMBERS, workspace_id),
        "search_events": await _rows(session, _SEARCH_EVENTS, workspace_id),
        "search_ratings": await _rows(session, _SEARCH_RATINGS, workspace_id),
        "unavailable_datasets": {},
    }
    return {
        **canonical_payload,
        "canonical_hash_algorithm": "sha256",
        "canonical_hash": _canonical_hash(canonical_payload),
        "exported_at": datetime.now(UTC),
    }
