"""
Founder/admin data access (D-067).

GET /v1/workspaces/:id/export  → the workspace's data as one JSON document; recorded in the audit
GET /v1/workspaces/:id/audit   → who changed membership, roles and invitations, newest first
"""

import time
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query

from sourcemind.core.dependencies import (
    CurrentUser,
    DBSession,
    RequestID,
    WorkspacePermission,
    require_workspace_permission,
)
from sourcemind.core.rate_limit import RateLimitedOperation, enforce_rate_limit
from sourcemind.schemas.common import APIResponse, ResponseMeta
from sourcemind.services.workspace import audit, export

router = APIRouter(tags=["workspace-admin"])


def _meta(request_id: str, start: float) -> ResponseMeta:
    return ResponseMeta(
        request_id=request_id,
        timestamp=datetime.now(UTC),
        latency_ms=(time.perf_counter() - start) * 1000,
    )


@router.get(
    "/workspaces/{workspace_id}/export",
    response_model=APIResponse[dict[str, Any]],
    summary="Export this workspace's data (founders and admins)",
)
async def export_workspace_data(
    workspace_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> APIResponse[dict[str, Any]]:
    start = time.perf_counter()
    caller_id = current_user.user_id
    await require_workspace_permission(db, caller_id, workspace_id, WorkspacePermission.ADMINISTER)
    await enforce_rate_limit(RateLimitedOperation.EXPORT, caller_id, workspace_id)
    data = await export.export_workspace(db, workspace_id)
    await audit.record(
        db, workspace_id=workspace_id, actor_id=caller_id, event="workspace_exported"
    )
    await db.commit()
    return APIResponse(data=data, meta=_meta(request_id, start))


@router.get(
    "/workspaces/{workspace_id}/audit",
    response_model=APIResponse[list[dict[str, Any]]],
    summary="Audit trail of membership, role and invitation changes (founders and admins)",
)
async def list_workspace_audit(
    workspace_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
    limit: Annotated[int, Query(ge=1, le=audit.MAX_PAGE)] = 50,
    before_id: Annotated[int | None, Query(ge=1)] = None,
) -> APIResponse[list[dict[str, Any]]]:
    start = time.perf_counter()
    await require_workspace_permission(
        db, current_user.user_id, workspace_id, WorkspacePermission.ADMINISTER
    )
    rows = await audit.list_events(db, workspace_id=workspace_id, before_id=before_id, limit=limit)
    return APIResponse(data=rows, meta=_meta(request_id, start))
