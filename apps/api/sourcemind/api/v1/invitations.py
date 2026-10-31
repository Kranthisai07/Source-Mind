"""
Workspace invitation endpoints (D-065).

POST   /v1/workspaces/:id/invitations       → invite an email; the token is returned once
GET    /v1/workspaces/:id/invitations       → pending invitations (never with tokens)
DELETE /v1/workspaces/:id/invitations/:iid  → revoke a pending invitation
POST   /v1/invitations/accept               → the invited person joins with the token
"""

import time
from datetime import UTC, datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, status

from sourcemind.core.config import get_settings
from sourcemind.core.dependencies import (
    CurrentUser,
    DBSession,
    IdempotencyKey,
    RequestID,
    WorkspacePermission,
    fetch_clerk_primary_email_verification,
    require_workspace_permission,
)
from sourcemind.core.exceptions import InvitationNotFoundError
from sourcemind.core.rate_limit import RateLimitedOperation, enforce_rate_limit
from sourcemind.schemas.common import APIResponse, ResponseMeta
from sourcemind.schemas.invitation import (
    InvitationAccept,
    InvitationAccepted,
    InvitationCreate,
    InvitationCreated,
    InvitationResponse,
)
from sourcemind.services.workspace import invitations as service

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["invitations"])


def _make_meta(request_id: str, start: float) -> ResponseMeta:
    return ResponseMeta(
        request_id=request_id,
        timestamp=datetime.now(UTC),
        latency_ms=(time.perf_counter() - start) * 1000,
    )


@router.post(
    "/workspaces/{workspace_id}/invitations",
    status_code=status.HTTP_201_CREATED,
    response_model=APIResponse[InvitationCreated],
    summary="Invite someone to a workspace",
)
async def create_workspace_invitation(
    workspace_id: UUID,
    body: InvitationCreate,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
    idempotency_key: IdempotencyKey,
) -> APIResponse[InvitationCreated]:
    """
    Invite an email address, which need not have an account yet.

    The response carries the one-time token and the page path that accepts it; it cannot be read
    again. Founders may invite as any role, admins only as teammate or viewer. A second invitation
    for the same address replaces the first. No email is sent: the inviter shares the link.
    Idempotency-Key is validated, not de-duplicated.
    """
    start = time.perf_counter()
    caller_id = current_user.user_id
    await enforce_rate_limit(RateLimitedOperation.INVITE_CREATE, caller_id)
    caller_role = await require_workspace_permission(
        db, caller_id, workspace_id, WorkspacePermission.ADMINISTER
    )
    await enforce_rate_limit(
        RateLimitedOperation.INVITE_CREATE_WORKSPACE, workspace_id, workspace_id
    )
    invitation, token = await service.create_invitation(
        db,
        workspace_id=workspace_id,
        caller_id=caller_id,
        caller_role=caller_role,
        email=body.email,
        role=body.role,
    )
    created = InvitationCreated(
        **InvitationResponse.model_validate(invitation).model_dump(),
        token=token,
        accept_path=f"/invite/{token}",
    )
    return APIResponse(data=created, meta=_make_meta(request_id, start))


@router.get(
    "/workspaces/{workspace_id}/invitations",
    response_model=APIResponse[list[InvitationResponse]],
    summary="List pending invitations",
)
async def list_workspace_invitations(
    workspace_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> APIResponse[list[InvitationResponse]]:
    start = time.perf_counter()
    await require_workspace_permission(
        db, current_user.user_id, workspace_id, WorkspacePermission.ADMINISTER
    )
    rows = await service.list_pending_invitations(db, workspace_id=workspace_id)
    return APIResponse(
        data=[InvitationResponse.model_validate(row) for row in rows],
        meta=_make_meta(request_id, start),
    )


@router.delete(
    "/workspaces/{workspace_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a pending invitation",
)
async def revoke_workspace_invitation(
    workspace_id: UUID,
    invitation_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
    idempotency_key: IdempotencyKey,
) -> None:
    caller_role = await require_workspace_permission(
        db, current_user.user_id, workspace_id, WorkspacePermission.ADMINISTER
    )
    await service.revoke_invitation(
        db,
        workspace_id=workspace_id,
        invitation_id=invitation_id,
        caller_id=current_user.user_id,
        caller_role=caller_role,
    )


@router.post(
    "/invitations/accept",
    response_model=APIResponse[InvitationAccepted],
    summary="Accept an invitation",
)
async def accept_workspace_invitation(
    body: InvitationAccept,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
    idempotency_key: IdempotencyKey,
) -> APIResponse[InvitationAccepted]:
    """
    Join the workspace a token names. The signed-in user's Clerk-verified primary email must equal
    the invited address. Any reason the invitation cannot be used gives the same 404.
    """
    start = time.perf_counter()
    user_id = current_user.user_id
    await enforce_rate_limit(RateLimitedOperation.INVITE_ACCEPT, user_id)

    settings = get_settings()
    if settings.auth_dev_bypass_enabled:
        # No Clerk to ask; the flag is rejected by Settings outside development.
        verified_email = str(current_user.email)
    else:
        clerk_email, verification = await fetch_clerk_primary_email_verification(
            current_user.clerk_id, settings.clerk_secret_key
        )
        if verification != "verified":
            raise InvitationNotFoundError()
        verified_email = clerk_email

    workspace_id, role = await service.accept_invitation(
        db, user_id=user_id, verified_email=verified_email, token=body.token
    )
    return APIResponse(
        data=InvitationAccepted(workspace_id=workspace_id, role=role),
        meta=_make_meta(request_id, start),
    )
