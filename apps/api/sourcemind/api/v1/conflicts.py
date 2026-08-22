"""
Conflict resolution endpoints.

GET  /v1/workspaces/:id/conflicts           — list workspace conflicts
GET  /v1/conflicts/:id                      — full conflict detail
POST /v1/conflicts/:id/review               — open → under_review
POST /v1/conflicts/:id/resolve              — apply resolution decision
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Query, status
from pydantic import BaseModel
from sqlalchemy import text

from sourcemind.core.dependencies import (
    CurrentUser,
    DBSession,
    OpenAIClient,
    RequestID,
)
from sourcemind.schemas.conflict import (
    ConflictDetail,
    ConflictListResponse,
    ConflictResolveResponse,
    ConflictReviewResponse,
    ConflictSummary,
)

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["conflicts"])


@router.get("/workspaces/{workspace_id}/conflicts", response_model=ConflictListResponse)
async def list_workspace_conflicts(
    workspace_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
    conflict_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> ConflictListResponse:
    """
    List conflicts for a workspace, ordered by similarity_score DESC.
    """
    conditions = ["mc.workspace_id = CAST(:ws_id AS uuid)"]
    params: dict[str, Any] = {"ws_id": str(workspace_id), "limit": limit}

    if conflict_status:
        conditions.append("mc.status = :status")
        params["status"] = conflict_status
    if cursor:
        conditions.append("mc.similarity_score < :cursor_score")
        params["cursor_score"] = float(cursor)

    where = " AND ".join(conditions)

    # S608 is a false positive here. `where` is a join of string literals
    # defined above in this function; every user-supplied value (status,
    # cursor) is bound through `params` as :status / :cursor_score and
    # never reaches the SQL text. The directive sits after the closing
    # quotes because that is where ruff reads it for a multi-line string -
    # putting it on the opening line makes it part of the query.
    sql = f"""
        SELECT
            mc.id::text, mc.status, mc.conflict_type, mc.severity,
            mc.similarity_score, mc.explanation,
            mc.memory_a_id::text, ma.content AS content_a,
            mc.memory_b_id::text, mb.content AS content_b,
            mc.created_at
        FROM memory_conflicts mc
        JOIN memories ma ON ma.id = mc.memory_a_id
        JOIN memories mb ON mb.id = mc.memory_b_id
        WHERE {where}
        ORDER BY mc.similarity_score DESC
        LIMIT :limit
    """  # noqa: S608
    result = await db.execute(text(sql), params)
    rows = result.fetchall()

    conflicts = [
        ConflictSummary(
            id=row[0],
            status=row[1],
            conflict_type=row[2],
            severity=row[3],
            similarity_score=row[4],
            explanation=row[5],
            memory_a_id=row[6],
            memory_a_content=row[7][:200] + "..." if len(row[7]) > 200 else row[7],
            memory_b_id=row[8],
            memory_b_content=row[9][:200] + "..." if len(row[9]) > 200 else row[9],
            created_at=row[10],
        )
        for row in rows
    ]

    next_cursor = str(rows[-1][4]) if rows else None
    return ConflictListResponse(conflicts=conflicts, total=len(conflicts), next_cursor=next_cursor)


@router.get("/conflicts/{conflict_id}", response_model=ConflictDetail)
async def get_conflict(
    conflict_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> ConflictDetail:
    """Full conflict detail: both memory contents and the neutral
    detection-time summary. No AI recommendation is produced (ADR-010).
    """
    from sourcemind.services.conflict.resolver import get_conflict_detail

    detail = await get_conflict_detail(db, conflict_id)
    if not detail:
        from sourcemind.core.exceptions import SourceMindError
        raise SourceMindError(f"Conflict {conflict_id} not found.", code="SM040")

    from sourcemind.schemas.conflict import MemoryRef
    return ConflictDetail(
        id=detail.conflict_id,
        status=detail.status,
        conflict_type=detail.conflict_type,
        severity=detail.severity,
        similarity_score=detail.similarity_score,
        explanation=detail.explanation,
        memory_a=MemoryRef(id=detail.memory_a_id, content=detail.memory_a_content),
        memory_b=MemoryRef(id=detail.memory_b_id, content=detail.memory_b_content),
        reviewed_by=detail.reviewed_by,
        reviewed_at=detail.reviewed_at,
        revisit_at=detail.revisit_at,
        created_at=detail.created_at,
    )


@router.post(
    "/conflicts/{conflict_id}/review",
    status_code=status.HTTP_200_OK,
    response_model=ConflictReviewResponse,
)
async def review_conflict(
    conflict_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> ConflictReviewResponse:
    """Transition conflict from open → under_review."""
    from sourcemind.services.conflict.resolver import mark_under_review

    ok = await mark_under_review(db, conflict_id, current_user.user_id)
    if not ok:
        return ConflictReviewResponse(status="no_change", conflict_id=conflict_id)

    await db.commit()
    return ConflictReviewResponse(status="under_review", conflict_id=conflict_id)


class ResolveBody(BaseModel):
    resolution_type: str
    resolution_note: str | None = None
    merged_content: str | None = None
    revisit_at: datetime | None = None
    tag_a: str | None = None
    tag_b: str | None = None


@router.post(
    "/conflicts/{conflict_id}/resolve",
    status_code=status.HTTP_200_OK,
    response_model=ConflictResolveResponse,
)
async def resolve_conflict_endpoint(
    conflict_id: uuid.UUID,
    body: ResolveBody,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
    openai_client: OpenAIClient,
) -> ConflictResolveResponse:
    """Apply a resolution decision to a conflict.

    Requires owner or admin on the workspace the conflict belongs to.
    Resolving retires memories and can create a merged one, so it is the only
    conflict action that is gated — listing, viewing detail and marking a
    conflict under review stay open to any workspace member.
    """
    from sourcemind.core.dependencies import require_workspace_role
    from sourcemind.models.workspace import WorkspaceRole
    from sourcemind.services.conflict.resolver import resolve_conflict

    workspace_id = (
        await db.execute(
            text(
                "SELECT workspace_id FROM memory_conflicts "
                "WHERE id = CAST(:cid AS uuid)"
            ),
            {"cid": str(conflict_id)},
        )
    ).scalar()
    if workspace_id is None:
        from sourcemind.core.exceptions import SourceMindError

        raise SourceMindError(f"Conflict {conflict_id} not found.", code="SM040")

    await require_workspace_role(
        db,
        current_user.user_id,
        workspace_id,
        {WorkspaceRole.OWNER.value, WorkspaceRole.ADMIN.value},
    )

    ok = await resolve_conflict(
        session=db,
        conflict_id=conflict_id,
        resolver_id=current_user.user_id,
        resolution_type=body.resolution_type,
        resolution_note=body.resolution_note,
        merged_content=body.merged_content,
        revisit_at=body.revisit_at,
        tag_a=body.tag_a,
        tag_b=body.tag_b,
        # Required so a 'merged' resolution produces a searchable memory.
        openai_client=openai_client,
    )

    if not ok:
        from sourcemind.core.exceptions import SourceMindError
        raise SourceMindError(f"Conflict {conflict_id} not found.", code="SM040")

    await db.commit()
    return ConflictResolveResponse(status="ok", resolution_type=body.resolution_type)
