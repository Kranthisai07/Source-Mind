"""Admin-only, read-only data export for the manual pilot."""

import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter

from sourcemind.core.dependencies import (
    CurrentUser,
    DBSession,
    RequestID,
    WorkspacePermission,
    require_workspace_permission,
)
from sourcemind.schemas.common import APIResponse, ResponseMeta

router = APIRouter(tags=["pilot"])


@router.get(
    "/workspaces/{workspace_id}/pilot-export",
    response_model=APIResponse[dict[str, Any]],
    summary="Export one workspace's pilot audit data",
)
async def get_pilot_export(
    workspace_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> APIResponse[dict[str, Any]]:
    start = time.perf_counter()
    await require_workspace_permission(
        db,
        current_user.user_id,
        workspace_id,
        WorkspacePermission.ADMINISTER,
    )

    from sourcemind.services.pilot_export import export_workspace_pilot_data

    export = await export_workspace_pilot_data(db, workspace_id)
    return APIResponse(
        data=export,
        meta=ResponseMeta(
            request_id=request_id,
            timestamp=datetime.now(UTC),
            latency_ms=(time.perf_counter() - start) * 1000,
        ),
    )
