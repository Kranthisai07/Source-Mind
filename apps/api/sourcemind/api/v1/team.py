"""
Team management endpoints.

GET  /v1/team/me                                   → current user profile
GET  /v1/team/members                              → list organization members
POST /v1/team/invite                               → invite a new member

POST /v1/workspaces/:id/handoff/initiate           → start knowledge handoff
POST /v1/workspaces/:id/handoff/assign             → assign a memory to new owner
POST /v1/workspaces/:id/handoff/complete           → finalize departure
"""

import time
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, status
from pydantic import BaseModel
from sqlalchemy import select, text

from sourcemind.core.dependencies import CurrentUser, DBSession, IdempotencyKey, RequestID
from sourcemind.core.exceptions import UserNotFoundError
from sourcemind.models.user import User
from sourcemind.schemas.common import APIResponse, ResponseMeta
from sourcemind.schemas.conflict import (
    HandoffAssignResponse,
    HandoffCompleteResponse,
    HandoffInitiateResponse,
    TierOneCriticalMemory,
    TierTwoMemory,
)
from sourcemind.schemas.user import UserResponse

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/team", tags=["team"])

# Workspace-scoped handoff endpoints (no prefix — full paths defined inline)
handoff_router = APIRouter(tags=["handoff"])


@router.get(
    "/me",
    response_model=APIResponse[UserResponse],
    summary="Get current user profile",
)
async def get_current_user_profile(
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> APIResponse[UserResponse]:
    """Return the authenticated user's profile."""
    start = time.perf_counter()

    result = await db.execute(
        select(User).where(User.id == current_user.user_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise UserNotFoundError(f"User {current_user.user_id} not found.")

    return APIResponse(
        data=UserResponse.model_validate(user),
        meta=ResponseMeta(
            request_id=request_id,
            timestamp=datetime.now(timezone.utc),
            latency_ms=(time.perf_counter() - start) * 1000,
        ),
    )


# ─── Handoff endpoints ────────────────────────────────────────────────────────


@handoff_router.get(
    "/workspaces/{workspace_id}/handoffs",
    summary="List handoff records for a workspace",
)
async def list_handoffs(
    workspace_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> dict:
    """Return all handoff records for the workspace, newest first."""
    result = await db.execute(
        text("""
            SELECT
                hr.id::text,
                hr.departing_user_id::text,
                COALESCE(u_dep.display_name, u_dep.email) AS departing_name,
                hr.initiated_by::text,
                COALESCE(u_init.display_name, u_init.email) AS initiated_by_name,
                hr.tier_1_count,
                hr.tier_2_count,
                hr.tier_3_count,
                hr.assigned_count,
                hr.status,
                hr.created_at,
                hr.expires_at,
                hr.completed_at,
                -- Most-assigned receiving user for this handoff record
                (
                    SELECT ha.new_owner_id::text
                    FROM handoff_assignments ha
                    WHERE ha.handoff_id = hr.id
                      AND ha.new_owner_id IS NOT NULL
                    GROUP BY ha.new_owner_id
                    ORDER BY COUNT(*) DESC
                    LIMIT 1
                ) AS receiving_user_id,
                (
                    SELECT COALESCE(u_recv.display_name, u_recv.email)
                    FROM handoff_assignments ha
                    JOIN users u_recv ON u_recv.id = ha.new_owner_id
                    WHERE ha.handoff_id = hr.id
                      AND ha.new_owner_id IS NOT NULL
                    GROUP BY ha.new_owner_id, u_recv.display_name, u_recv.email
                    ORDER BY COUNT(*) DESC
                    LIMIT 1
                ) AS receiving_user_name
            FROM handoff_records hr
            LEFT JOIN users u_dep ON u_dep.id = hr.departing_user_id
            LEFT JOIN users u_init ON u_init.id = hr.initiated_by
            WHERE hr.workspace_id = :ws::uuid
            ORDER BY hr.created_at DESC
        """),
        {"ws": str(workspace_id)},
    )
    rows = result.fetchall()
    return {
        "handoffs": [
            {
                "id": r[0],
                "departing_user_id": r[1],
                "departing_user_name": r[2],
                "initiated_by": r[3],
                "initiated_by_name": r[4],
                "tier_1_count": r[5],
                "tier_2_count": r[6],
                "tier_3_count": r[7],
                "assigned_count": r[8],
                "status": r[9],
                "created_at": str(r[10]) if r[10] else None,
                "expires_at": str(r[11]) if r[11] else None,
                "completed_at": str(r[12]) if r[12] else None,
                "receiving_user_id": r[13],
                "receiving_user_name": r[14],
            }
            for r in rows
        ]
    }


class InitiateHandoffBody(BaseModel):
    departing_user_id: uuid.UUID


class AssignHandoffBody(BaseModel):
    memory_id: uuid.UUID
    new_owner_id: uuid.UUID
    handoff_record_id: uuid.UUID
    note: str | None = None


class CompleteHandoffBody(BaseModel):
    departing_user_id: uuid.UUID
    handoff_record_id: uuid.UUID


@handoff_router.post(
    "/workspaces/{workspace_id}/handoff/initiate",
    status_code=status.HTTP_200_OK,
    response_model=HandoffInitiateResponse,
    summary="Initiate knowledge handoff for a departing member",
)
async def initiate_handoff(
    workspace_id: uuid.UUID,
    body: InitiateHandoffBody,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> HandoffInitiateResponse:
    """
    Classify all memories belonging to the departing user into tiers,
    generate successor suggestions for Tier 1 memories, and set the
    workspace member status to 'departing'.

    Returns a HandoffSummary with tier breakdown and suggested successors.
    """
    from sourcemind.services.attribution.handoff import (
        classify_memories,
        create_handoff_record,
    )

    summary = await classify_memories(db, workspace_id, body.departing_user_id)
    handoff_id = await create_handoff_record(
        session=db,
        workspace_id=workspace_id,
        departing_user_id=body.departing_user_id,
        initiated_by=current_user.user_id,
        summary=summary,
    )
    await db.commit()

    summary.handoff_record_id = str(handoff_id)

    return HandoffInitiateResponse(
        handoff_record_id=handoff_id,
        departing_user_id=summary.departing_user_id,
        departing_user_name=summary.departing_user_name,
        total_memories=summary.total_memories_with_attribution,
        tier_1_critical=[
            TierOneCriticalMemory(
                memory_id=m.memory_id,
                content=m.content,
                importance_score=m.importance_score,
                suggested_successor_id=m.suggested_successor_id,
                suggested_successor_name=m.suggested_successor_name,
                successor_confidence=m.successor_confidence,
            )
            for m in summary.tier_1_critical
        ],
        tier_2_important=[
            TierTwoMemory(
                memory_id=m.memory_id,
                content=m.content,
                importance_score=m.importance_score,
            )
            for m in summary.tier_2_important
        ],
        tier_3_standard_count=summary.tier_3_standard_count,
    )


@handoff_router.post(
    "/workspaces/{workspace_id}/handoff/assign",
    status_code=status.HTTP_200_OK,
    response_model=HandoffAssignResponse,
    summary="Assign a specific memory to a new owner during handoff",
)
async def assign_handoff_memory(
    workspace_id: uuid.UUID,
    body: AssignHandoffBody,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> HandoffAssignResponse:
    """
    Transfer 40% of the departing user's attribution share for a memory
    to the new owner. Attribution records are append-only.
    """
    from sourcemind.services.attribution.handoff import assign_memory

    # Resolve departing user from handoff_records
    from sqlalchemy import text
    hr_result = await db.execute(
        text("SELECT departing_user_id FROM handoff_records WHERE id = :hid::uuid"),
        {"hid": str(body.handoff_record_id)},
    )
    hr_row = hr_result.fetchone()
    if not hr_row:
        from sourcemind.core.exceptions import SourceMindError
        raise SourceMindError("Handoff record not found.", code="SM050")

    departing_user_id = hr_row[0]

    result = await assign_memory(
        session=db,
        handoff_record_id=body.handoff_record_id,
        memory_id=body.memory_id,
        new_owner_id=body.new_owner_id,
        departing_user_id=departing_user_id,
        note=body.note,
    )
    await db.commit()
    from sourcemind.schemas.conflict import AttributionBreakdownItem
    return HandoffAssignResponse(
        memory_id=result["memory_id"],
        attribution=[AttributionBreakdownItem(**item) for item in result["attribution"]],
    )


@handoff_router.post(
    "/workspaces/{workspace_id}/handoff/complete",
    status_code=status.HTTP_200_OK,
    response_model=HandoffCompleteResponse,
    summary="Finalize the handoff and mark member as departed",
)
async def complete_handoff_endpoint(
    workspace_id: uuid.UUID,
    body: CompleteHandoffBody,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> HandoffCompleteResponse:
    """
    Mark the departing member's status as 'departed', close the handoff record,
    and report any unassigned Tier 1 memories.
    """
    from sourcemind.services.attribution.handoff import complete_handoff

    result = await complete_handoff(
        session=db,
        workspace_id=workspace_id,
        departing_user_id=body.departing_user_id,
        handoff_record_id=body.handoff_record_id,
    )
    await db.commit()
    return HandoffCompleteResponse(
        status=result["status"],
        departing_user_id=result["departing_user_id"],
        unassigned_tier_1_count=result["unassigned_tier_1_count"],
    )
