"""
Workspace resource endpoints.

GET  /v1/workspaces              → list workspaces for current user
POST /v1/workspaces              → create workspace (auto-creates org if needed)
GET  /v1/workspaces/:id          → get workspace details
GET  /v1/workspaces/:id/members  → list members with roles
GET  /v1/workspaces/:id/analytics → contribution map + knowledge gaps
"""

import time
import uuid
from datetime import datetime, timezone
from uuid import UUID

import structlog
from fastapi import APIRouter, status
from sqlalchemy import select

from sourcemind.core.dependencies import CurrentUser, DBSession, IdempotencyKey, RequestID
from sourcemind.core.exceptions import WorkspaceNotFoundError
from sourcemind.models.organization import Organization
from sourcemind.models.user import User
from sourcemind.models.workspace import Workspace, WorkspaceMember, WorkspaceRole
from sourcemind.schemas.common import APIResponse, ResponseMeta
from sourcemind.schemas.user import UserSummary
from sourcemind.schemas.workspace import (
    WorkspaceCreate,
    WorkspaceMemberResponse,
    WorkspaceResponse,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _make_meta(request_id: str, start: float) -> ResponseMeta:
    return ResponseMeta(
        request_id=request_id,
        timestamp=datetime.now(timezone.utc),
        latency_ms=(time.perf_counter() - start) * 1000,
    )


@router.get(
    "",
    response_model=APIResponse[list[WorkspaceResponse]],
    summary="List workspaces for current user",
)
async def list_workspaces(
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> APIResponse[list[WorkspaceResponse]]:
    """Return all workspaces the current user is a member of."""
    start = time.perf_counter()

    result = await db.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            WorkspaceMember.user_id == current_user.user_id,
            Workspace.deleted_at.is_(None),
        )
        .order_by(Workspace.created_at.asc())
    )
    workspaces = result.scalars().all()

    return APIResponse(
        data=[WorkspaceResponse.model_validate(ws) for ws in workspaces],
        meta=_make_meta(request_id, start),
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=APIResponse[WorkspaceResponse],
    summary="Create a new workspace",
)
async def create_workspace(
    body: WorkspaceCreate,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
    idempotency_key: IdempotencyKey,
) -> APIResponse[WorkspaceResponse]:
    """
    Create a workspace. If the user has no organization, one is auto-created.
    The creating user becomes the workspace owner.
    """
    start = time.perf_counter()

    # Find or auto-create an organization for this user
    # Look for an org where the user already owns a workspace
    existing_org_result = await db.execute(
        select(Organization)
        .join(Workspace, Workspace.organization_id == Organization.id)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            WorkspaceMember.user_id == current_user.user_id,
            WorkspaceMember.role == WorkspaceRole.OWNER,
            Organization.deleted_at.is_(None),
        )
        .limit(1)
    )
    org = existing_org_result.scalar_one_or_none()

    if org is None:
        # Auto-create a personal organization using the user's email prefix as slug
        email_prefix = current_user.email.split("@")[0]
        org_slug = f"{email_prefix}-org"
        # Ensure slug uniqueness
        collision = await db.execute(
            select(Organization).where(Organization.slug == org_slug)
        )
        if collision.scalar_one_or_none():
            org_slug = f"{email_prefix}-{str(uuid.uuid4())[:8]}"

        org = Organization(
            name=f"{current_user.display_name or email_prefix}'s Organization",
            slug=org_slug,
        )
        db.add(org)
        await db.flush()
        logger.info("workspace.org_auto_created", org_id=str(org.id), slug=org_slug)

    # Create the workspace
    workspace = Workspace(
        organization_id=org.id,
        name=body.name,
        slug=body.slug,
        description=body.description,
    )
    db.add(workspace)
    await db.flush()

    # Add creator as owner
    membership = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=current_user.user_id,
        role=WorkspaceRole.OWNER,
    )
    db.add(membership)
    await db.commit()
    await db.refresh(workspace)

    logger.info(
        "workspace.created",
        workspace_id=str(workspace.id),
        slug=workspace.slug,
        user_id=str(current_user.user_id),
    )

    return APIResponse(
        data=WorkspaceResponse.model_validate(workspace),
        meta=_make_meta(request_id, start),
    )


@router.get(
    "/{workspace_id}",
    response_model=APIResponse[WorkspaceResponse],
    summary="Get workspace details",
)
async def get_workspace(
    workspace_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> APIResponse[WorkspaceResponse]:
    """Retrieve workspace details. User must be a member."""
    start = time.perf_counter()

    result = await db.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            Workspace.id == workspace_id,
            WorkspaceMember.user_id == current_user.user_id,
            Workspace.deleted_at.is_(None),
        )
    )
    workspace = result.scalar_one_or_none()
    if workspace is None:
        raise WorkspaceNotFoundError(str(workspace_id))

    return APIResponse(
        data=WorkspaceResponse.model_validate(workspace),
        meta=_make_meta(request_id, start),
    )


@router.get(
    "/{workspace_id}/members",
    response_model=APIResponse[list[WorkspaceMemberResponse]],
    summary="List workspace members",
)
async def list_workspace_members(
    workspace_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> APIResponse[list[WorkspaceMemberResponse]]:
    """List all members of a workspace with their roles."""
    start = time.perf_counter()

    # Caller must be a member of the workspace
    access_check = await db.execute(
        select(WorkspaceMember.id).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == current_user.user_id,
        )
    )
    if access_check.scalar_one_or_none() is None:
        raise WorkspaceNotFoundError(str(workspace_id))

    result = await db.execute(
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.created_at.asc())
    )
    members = [
        WorkspaceMemberResponse(
            user=UserSummary.model_validate(user),
            role=WorkspaceRole(member.role),
            joined_at=member.created_at,
        )
        for member, user in result.all()
    ]

    return APIResponse(data=members, meta=_make_meta(request_id, start))
