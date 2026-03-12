"""
Team management endpoints.

GET  /v1/team/me                                   → current user profile
GET  /v1/team/members                              → list organization members
POST /v1/team/invite                               → invite a new member

POST /v1/workspaces/:id/handoff/initiate           → start knowledge handoff
POST /v1/workspaces/:id/handoff/assign             → assign a memory to new owner
POST /v1/workspaces/:id/handoff/complete           → finalize departure
"""

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, status
from pydantic import BaseModel

from sourcemind.core.dependencies import CurrentUser, DBSession, IdempotencyKey, RequestID
from sourcemind.core.exceptions import NotImplementedFeatureError
from sourcemind.schemas.common import APIResponse
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
    raise NotImplementedFeatureError(
        "User profile endpoint will be implemented in Phase 2."
    )


# ─── Handoff endpoints ────────────────────────────────────────────────────────


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
    summary="Initiate knowledge handoff for a departing member",
)
async def initiate_handoff(
    workspace_id: uuid.UUID,
    body: InitiateHandoffBody,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> dict[str, Any]:
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

    return {
        "handoff_record_id": str(handoff_id),
        "departing_user_id": summary.departing_user_id,
        "departing_user_name": summary.departing_user_name,
        "total_memories": summary.total_memories_with_attribution,
        "tier_1_critical": [
            {
                "memory_id": m.memory_id,
                "content": m.content,
                "importance_score": m.importance_score,
                "suggested_successor_id": m.suggested_successor_id,
                "suggested_successor_name": m.suggested_successor_name,
                "successor_confidence": m.successor_confidence,
            }
            for m in summary.tier_1_critical
        ],
        "tier_2_important": [
            {
                "memory_id": m.memory_id,
                "content": m.content,
                "importance_score": m.importance_score,
            }
            for m in summary.tier_2_important
        ],
        "tier_3_standard_count": summary.tier_3_standard_count,
    }


@handoff_router.post(
    "/workspaces/{workspace_id}/handoff/assign",
    status_code=status.HTTP_200_OK,
    summary="Assign a specific memory to a new owner during handoff",
)
async def assign_handoff_memory(
    workspace_id: uuid.UUID,
    body: AssignHandoffBody,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> dict[str, Any]:
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
    return result


@handoff_router.post(
    "/workspaces/{workspace_id}/handoff/complete",
    status_code=status.HTTP_200_OK,
    summary="Finalize the handoff and mark member as departed",
)
async def complete_handoff_endpoint(
    workspace_id: uuid.UUID,
    body: CompleteHandoffBody,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> dict[str, Any]:
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
    return result
