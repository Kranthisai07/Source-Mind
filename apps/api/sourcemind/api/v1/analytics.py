"""
Workspace analytics endpoints.

GET /v1/workspaces/:id/analytics/overview
GET /v1/workspaces/:id/analytics/contribution-map
GET /v1/workspaces/:id/analytics/knowledge-gaps
GET /v1/workspaces/:id/analytics/who-would-know?q=<query>
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Query

from sourcemind.core.dependencies import CurrentUser, DBSession, RequestID

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["analytics"])


@router.get("/workspaces/{workspace_id}/analytics/overview")
async def workspace_overview(
    workspace_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> dict[str, Any]:
    """
    Workspace analytics overview — cached for 5 minutes in Redis.

    Returns health score, top contributors, recent activity.
    """
    from sourcemind.services.analytics.workspace import get_overview

    return await get_overview(db, workspace_id)


@router.get("/workspaces/{workspace_id}/analytics/contribution-map")
async def contribution_map(
    workspace_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> dict[str, Any]:
    """Per-contributor breakdown of knowledge ownership and collaboration."""
    from sourcemind.services.analytics.workspace import get_contribution_map

    return await get_contribution_map(db, workspace_id)


@router.get("/workspaces/{workspace_id}/analytics/knowledge-gaps")
async def knowledge_gaps(
    workspace_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> dict[str, Any]:
    """
    Identify knowledge risks:
      - single_contributor: one person owns >90% of a memory
      - no_recent_update: important memories never refreshed after 90 days
      - high_conflict_area: tag clusters with >20% open conflict rate
    """
    from sourcemind.services.analytics.workspace import get_knowledge_gaps

    return await get_knowledge_gaps(db, workspace_id)


@router.get("/workspaces/{workspace_id}/analytics/who-would-know")
async def who_would_know(
    workspace_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
    q: str = Query(..., min_length=2, max_length=200, description="Search query"),
    limit: int = Query(5, ge=1, le=10),
) -> dict[str, Any]:
    """
    Reverse-expertise search: given a free-text question, return the contributors
    most likely to have relevant knowledge, ranked by weighted attribution score
    across BM25-matching memories.
    """
    from sourcemind.services.analytics.workspace import who_would_know as _wwk

    return await _wwk(db, workspace_id, q, limit)
