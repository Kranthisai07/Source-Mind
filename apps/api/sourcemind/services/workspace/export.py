"""Admin export of one workspace's data (D-067): the in-app form of "admin access to the database".

Reuses the pilot export's queries for documents, memories, versions, attribution, relations,
conflicts and membership, so the two exports cannot drift on what a workspace's data is. Added:
member names and sign-in emails, and pending invitations without their token hashes.

Never exported: embeddings, Clerk identifiers or payloads, invitation token hashes, MCP key
hashes, connector credentials, search events and ratings (pilot evidence has its own export), and
anything belonging to another workspace. Every query is filtered to the workspace explicitly.

The whole export is built in memory, like the pilot export. A workspace larger than
`MAX_ROWS_PER_DATASET` in any one dataset is refused rather than silently truncated.
"""

# ruff: noqa: S608

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.exceptions import ContentTooLargeError
from sourcemind.services import pilot_export as pilot

EXPORT_SCHEMA_VERSION = 1
MAX_ROWS_PER_DATASET = 100_000

_USERS = """
SELECT
    u.id::text AS id,
    u.display_name,
    u.email
FROM users AS u
WHERE u.id IN (
    SELECT user_id FROM workspace_members WHERE workspace_id = CAST(:workspace_id AS uuid)
)
ORDER BY u.id
"""

_PENDING_INVITATIONS = """
SELECT
    id::text AS id,
    email,
    role,
    invited_by::text AS invited_by,
    created_at,
    expires_at
FROM workspace_invitations
WHERE workspace_id = CAST(:workspace_id AS uuid)
  AND accepted_at IS NULL
  AND revoked_at IS NULL
  AND expires_at > now()
ORDER BY created_at, id
"""

_DATASETS: tuple[tuple[str, str], ...] = (
    ("documents", pilot._DOCUMENTS),
    ("memories", pilot._MEMORIES),
    ("versions", pilot._VERSIONS),
    ("attributions", pilot._ATTRIBUTIONS),
    ("attribution_edits", pilot._ATTRIBUTION_EDITS),
    ("relations", pilot._RELATIONS),
    ("conflicts", pilot._CONFLICTS),
    ("workspace_members", pilot._WORKSPACE_MEMBERS),
    ("users", _USERS),
    ("pending_invitations", _PENDING_INVITATIONS),
)


async def _capped_rows(
    session: AsyncSession, name: str, statement: str, workspace_id: uuid.UUID
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(f"SELECT * FROM ({statement}) AS dataset LIMIT {MAX_ROWS_PER_DATASET + 1}"),
        {"workspace_id": str(workspace_id)},
    )
    rows = [dict(row) for row in result.mappings().all()]
    if len(rows) > MAX_ROWS_PER_DATASET:
        raise ContentTooLargeError(
            f"This workspace is too large to export in one request ({name}).",
            details={"reason": "export_too_large", "dataset": name},
        )
    return rows


async def export_workspace(session: AsyncSession, workspace_id: uuid.UUID) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "workspace_id": str(workspace_id),
    }
    for name, statement in _DATASETS:
        payload[name] = await _capped_rows(session, name, statement, workspace_id)
    return {
        **payload,
        "canonical_hash_algorithm": "sha256",
        "canonical_hash": pilot._canonical_hash(payload),
        "exported_at": datetime.now(UTC),
    }
