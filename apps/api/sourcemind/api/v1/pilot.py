"""Admin-only pilot evidence and export endpoints."""

import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter

from sourcemind.core.dependencies import (
    CurrentUser,
    DBSession,
    IdempotencyKey,
    RequestID,
    WorkspacePermission,
    require_workspace_permission,
)
from sourcemind.schemas.common import APIResponse, ResponseMeta
from sourcemind.schemas.pilot import SearchRatingCreate, SearchRatingResponse

router = APIRouter(tags=["pilot"])


@router.post(
    "/workspaces/{workspace_id}/pilot-ratings",
    response_model=APIResponse[SearchRatingResponse],
    summary="Record an operator-managed pilot rating",
)
async def create_pilot_rating(
    workspace_id: UUID,
    body: SearchRatingCreate,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
    idempotency_key: IdempotencyKey,
) -> APIResponse[SearchRatingResponse]:
    start = time.perf_counter()
    await require_workspace_permission(
        db,
        current_user.user_id,
        workspace_id,
        WorkspacePermission.ADMINISTER,
    )

    from sourcemind.services.pilot_evidence import create_search_rating

    rating = await create_search_rating(
        db,
        workspace_id=workspace_id,
        recorded_by_user_id=current_user.user_id,
        idempotency_key=idempotency_key,
        body=body,
    )
    return APIResponse(
        data=SearchRatingResponse.model_validate(rating),
        meta=ResponseMeta(
            request_id=request_id,
            timestamp=datetime.now(UTC),
            latency_ms=(time.perf_counter() - start) * 1000,
        ),
    )


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
