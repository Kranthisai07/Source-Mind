"""Pydantic schemas for Connector resources."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# ─── Request schemas ──────────────────────────────────────────────────────────

class ConnectorCreateRequest(BaseModel):
    """Payload for creating a new connector configuration."""

    connector_type: str = Field(description="e.g. 'github' or 'discord'")
    display_name: str = Field(min_length=1, max_length=255)
    config: dict = Field(default_factory=dict, description="Connector-specific settings")


class ConnectorUpdateRequest(BaseModel):
    """Payload for updating a connector configuration."""

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    config: dict | None = Field(default=None)
    status: str | None = Field(default=None)


class SyncTriggerRequest(BaseModel):
    """Payload for manually triggering a connector sync."""

    sync_type: str = Field(default="incremental", description="'full' or 'incremental'")


# ─── Response schemas ─────────────────────────────────────────────────────────

class ConnectorResponse(BaseModel):
    """Single connector configuration response."""

    id: UUID
    workspace_id: UUID
    connector_type: str
    display_name: str
    config: dict
    status: str
    last_sync_at: datetime | None
    next_sync_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ConnectorListResponse(BaseModel):
    """List of connectors for a workspace."""

    items: list[ConnectorResponse]
    total: int


class SyncLogResponse(BaseModel):
    """Single sync log entry."""

    id: UUID
    connector_id: UUID
    sync_type: str
    status: str
    artifacts_found: int
    artifacts_new: int
    artifacts_skipped: int
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class SyncTriggerResponse(BaseModel):
    """Response after triggering a connector sync."""

    task_id: str
    connector_id: UUID
    sync_type: str
    message: str


class SyncLogsResponse(BaseModel):
    """Paginated sync logs for a connector."""

    items: list[SyncLogResponse]
    total: int
