"""Workspace audit trail (D-067): who changed membership, roles, invitations, or exported data.

Rows are written inside the same transaction as the change they describe, so a change and its
record commit or roll back together. The table is append-only and holds ids, roles and an invitee
address, never memory content, tokens or token hashes.

Call `record` BEFORE a change that could remove the actor's own authority (demoting yourself,
leaving the workspace): the insert policy requires the actor to be a current founder or admin.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

AuditEvent = Literal[
    "member_added",
    "member_removed",
    "role_changed",
    "invitation_created",
    "invitation_revoked",
    "invitation_accepted",
    "workspace_exported",
]

MAX_PAGE = 200


async def record(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID,
    event: AuditEvent,
    target_user_id: uuid.UUID | None = None,
    target_email: str | None = None,
    role_before: str | None = None,
    role_after: str | None = None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO workspace_audit_events "
            "(workspace_id, actor_user_id, event, target_user_id, target_email, "
            "role_before, role_after) "
            "VALUES (:workspace_id, :actor_id, :event, :target_user_id, :target_email, "
            ":role_before, :role_after)"
        ),
        {
            "workspace_id": workspace_id,
            "actor_id": actor_id,
            "event": event,
            "target_user_id": target_user_id,
            "target_email": target_email.lower() if target_email else None,
            "role_before": role_before,
            "role_after": role_after,
        },
    )


async def list_events(
    db: AsyncSession, *, workspace_id: uuid.UUID, before_id: int | None, limit: int
) -> list[dict[str, Any]]:
    """Newest first. `before_id` is the last id of the previous page."""
    limit = max(1, min(limit, MAX_PAGE))
    rows = (
        await db.execute(
            text(
                "SELECT e.id, e.event, e.actor_user_id, actor.display_name AS actor_name, "
                "e.target_user_id, target.display_name AS target_name, e.target_email, "
                "e.role_before, e.role_after, e.at "
                "FROM workspace_audit_events AS e "
                "LEFT JOIN users AS actor ON actor.id = e.actor_user_id "
                "LEFT JOIN users AS target ON target.id = e.target_user_id "
                "WHERE e.workspace_id = :workspace_id "
                "AND (CAST(:before_id AS bigint) IS NULL OR e.id < CAST(:before_id AS bigint)) "
                "ORDER BY e.id DESC LIMIT :limit"
            ),
            {"workspace_id": workspace_id, "before_id": before_id, "limit": limit},
        )
    ).mappings()
    return [dict(row) for row in rows]
