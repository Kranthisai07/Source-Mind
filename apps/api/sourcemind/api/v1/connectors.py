"""
Connector management endpoints.

POST   /v1/workspaces/:id/connectors            — create a connector
GET    /v1/workspaces/:id/connectors            — list connectors
GET    /v1/connectors/:id                       — get connector detail
PATCH  /v1/connectors/:id                       — update config/status
DELETE /v1/connectors/:id                       — delete connector
POST   /v1/connectors/:id/sync                  — trigger a manual sync
GET    /v1/connectors/:id/sync-logs             — paginated sync history
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Query, status
from sqlalchemy import func, select

from sourcemind.core.dependencies import CurrentUser, DBSession, RequestID
from sourcemind.models.connector import ConnectorConfig, ConnectorSyncLog
from sourcemind.schemas.connector import (
    ConnectorCreateRequest,
    ConnectorListResponse,
    ConnectorResponse,
    ConnectorUpdateRequest,
    SyncLogResponse,
    SyncLogsResponse,
    SyncTriggerRequest,
    SyncTriggerResponse,
)

log = structlog.get_logger(__name__)

router = APIRouter(tags=["connectors"])


# ── Create connector ──────────────────────────────────────────────────────────

@router.post(
    "/workspaces/{workspace_id}/connectors",
    response_model=ConnectorResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create connector",
)
async def create_connector(
    workspace_id: uuid.UUID,
    body: ConnectorCreateRequest,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> ConnectorResponse:
    """Register a new connector configuration for the workspace."""
    connector = ConnectorConfig(
        workspace_id=workspace_id,
        connector_type=body.connector_type,
        display_name=body.display_name,
        config=body.config,
        status="active",
    )
    db.add(connector)
    await db.flush()
    await db.refresh(connector)
    await db.commit()

    log.info(
        "connector_created",
        connector_id=str(connector.id),
        connector_type=body.connector_type,
        workspace_id=str(workspace_id),
        request_id=request_id,
    )
    return ConnectorResponse.model_validate(connector)


# ── List connectors ───────────────────────────────────────────────────────────

@router.get(
    "/workspaces/{workspace_id}/connectors",
    response_model=ConnectorListResponse,
    summary="List connectors",
)
async def list_connectors(
    workspace_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    connector_type: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
) -> ConnectorListResponse:
    """List all connectors for the given workspace, with optional filters."""
    query = select(ConnectorConfig).where(
        ConnectorConfig.workspace_id == workspace_id
    )
    if connector_type:
        query = query.where(ConnectorConfig.connector_type == connector_type)
    if status_filter:
        query = query.where(ConnectorConfig.status == status_filter)
    query = query.order_by(ConnectorConfig.created_at.desc())

    result = await db.execute(query)
    connectors = result.scalars().all()
    return ConnectorListResponse(
        items=[ConnectorResponse.model_validate(c) for c in connectors],
        total=len(connectors),
    )


# ── Get connector ─────────────────────────────────────────────────────────────

@router.get(
    "/connectors/{connector_id}",
    response_model=ConnectorResponse,
    summary="Get connector",
)
async def get_connector(
    connector_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> ConnectorResponse:
    """Retrieve a single connector configuration by ID."""
    result = await db.execute(
        select(ConnectorConfig).where(ConnectorConfig.id == connector_id)
    )
    connector = result.scalar_one_or_none()
    if connector is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Connector not found")
    return ConnectorResponse.model_validate(connector)


# ── Update connector ──────────────────────────────────────────────────────────

@router.patch(
    "/connectors/{connector_id}",
    response_model=ConnectorResponse,
    summary="Update connector",
)
async def update_connector(
    connector_id: uuid.UUID,
    body: ConnectorUpdateRequest,
    db: DBSession,
    current_user: CurrentUser,
) -> ConnectorResponse:
    """Update connector display name, config, or status."""
    result = await db.execute(
        select(ConnectorConfig).where(ConnectorConfig.id == connector_id)
    )
    connector = result.scalar_one_or_none()
    if connector is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Connector not found")

    if body.display_name is not None:
        connector.display_name = body.display_name
    if body.config is not None:
        connector.config = body.config
    if body.status is not None:
        connector.status = body.status

    await db.flush()
    await db.refresh(connector)
    await db.commit()
    return ConnectorResponse.model_validate(connector)


# ── Delete connector ──────────────────────────────────────────────────────────

@router.delete(
    "/connectors/{connector_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete connector",
)
async def delete_connector(
    connector_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> None:
    """Delete a connector and all its sync logs (cascade)."""
    result = await db.execute(
        select(ConnectorConfig).where(ConnectorConfig.id == connector_id)
    )
    connector = result.scalar_one_or_none()
    if connector is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Connector not found")

    await db.delete(connector)
    await db.commit()


# ── Trigger sync ──────────────────────────────────────────────────────────────

@router.post(
    "/connectors/{connector_id}/sync",
    response_model=SyncTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger connector sync",
)
async def trigger_sync(
    connector_id: uuid.UUID,
    body: SyncTriggerRequest,
    db: DBSession,
    current_user: CurrentUser,
) -> SyncTriggerResponse:
    """Enqueue a background sync task for the given connector."""
    from sourcemind.workers.connector_tasks import sync_github_connector

    result = await db.execute(
        select(ConnectorConfig).where(ConnectorConfig.id == connector_id)
    )
    connector = result.scalar_one_or_none()
    if connector is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Connector not found")

    task = sync_github_connector.apply_async(
        kwargs={
            "connector_id": str(connector_id),
            "workspace_id": str(connector.workspace_id),
            "user_id": str(current_user.user_id),
            "sync_type": body.sync_type,
        }
    )

    log.info(
        "connector_sync_triggered",
        connector_id=str(connector_id),
        sync_type=body.sync_type,
        task_id=task.id,
    )
    return SyncTriggerResponse(
        task_id=task.id,
        connector_id=connector_id,
        sync_type=body.sync_type,
        message=f"Sync task enqueued (type={body.sync_type})",
    )


# ── Sync logs ─────────────────────────────────────────────────────────────────

@router.get(
    "/connectors/{connector_id}/sync-logs",
    response_model=SyncLogsResponse,
    summary="List sync logs",
)
async def list_sync_logs(
    connector_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
) -> SyncLogsResponse:
    """Return paginated sync history for a connector."""
    count_result = await db.execute(
        select(func.count()).where(ConnectorSyncLog.connector_id == connector_id)
    )
    total = count_result.scalar_one()

    logs_result = await db.execute(
        select(ConnectorSyncLog)
        .where(ConnectorSyncLog.connector_id == connector_id)
        .order_by(ConnectorSyncLog.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    logs = logs_result.scalars().all()

    return SyncLogsResponse(
        items=[SyncLogResponse.model_validate(log_entry) for log_entry in logs],
        total=total,
    )
