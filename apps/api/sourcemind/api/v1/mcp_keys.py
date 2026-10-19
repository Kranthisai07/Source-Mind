"""Workspace-scoped MCP API key management endpoints."""

from __future__ import annotations

import uuid
from http import HTTPStatus

from fastapi import APIRouter, Response, status

from sourcemind.core.dependencies import (
    CurrentUser,
    DBSession,
    WorkspacePermission,
    require_workspace_permission,
)
from sourcemind.core.exceptions import ErrorCode, SourceMindError
from sourcemind.core.rate_limit import RateLimitedOperation, enforce_rate_limit
from sourcemind.mcp.settings import get_mcp_settings
from sourcemind.schemas.mcp_keys import McpKeyCreate, McpKeyCreated, McpKeyList, McpKeyOut
from sourcemind.services import mcp_keys

router = APIRouter(prefix="/workspaces/{workspace_id}/mcp-keys", tags=["mcp-keys"])


def _not_found() -> SourceMindError:
    return SourceMindError(
        "MCP API key not found.",
        code=ErrorCode.CONNECTOR_NOT_FOUND,
        http_status=HTTPStatus.NOT_FOUND,
    )


@router.post("", response_model=McpKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_mcp_key(
    workspace_id: uuid.UUID,
    body: McpKeyCreate,
    response: Response,
    db: DBSession,
    current_user: CurrentUser,
) -> McpKeyCreated:
    """Create a key for the active workspace member and return its secret once."""
    await require_workspace_permission(
        db, current_user.user_id, workspace_id, WorkspacePermission.READ
    )
    await enforce_rate_limit(
        RateLimitedOperation.MCP_KEY_CREATE, current_user.user_id, workspace_id
    )
    created = await mcp_keys.create_key(
        db,
        user_id=current_user.user_id,
        workspace_id=workspace_id,
        name=body.name,
        expires_in_days=body.expires_in_days,
    )
    response.headers["Cache-Control"] = "no-store"
    return McpKeyCreated(**created.__dict__)


@router.get("", response_model=McpKeyList)
async def list_mcp_keys(
    workspace_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> McpKeyList:
    """List all workspace keys for administrators, otherwise only the caller's keys."""
    role = await require_workspace_permission(
        db, current_user.user_id, workspace_id, WorkspacePermission.READ
    )
    records = await mcp_keys.list_keys(
        db,
        user_id=current_user.user_id,
        workspace_id=workspace_id,
        is_admin=role in {"owner", "admin"},
    )
    return McpKeyList(
        keys=[McpKeyOut(**record.__dict__) for record in records],
        mcp_enabled=get_mcp_settings().enabled,
    )


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_mcp_key(
    workspace_id: uuid.UUID,
    key_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> None:
    """Revoke a caller-owned key, or any workspace key for an administrator."""
    role = await require_workspace_permission(
        db, current_user.user_id, workspace_id, WorkspacePermission.READ
    )
    revoked = await mcp_keys.revoke_key(
        db,
        key_id=key_id,
        actor_user_id=current_user.user_id,
        workspace_id=workspace_id,
        is_admin=role in {"owner", "admin"},
    )
    if revoked is None:
        raise _not_found()
