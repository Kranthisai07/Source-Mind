"""Pydantic schemas for Connector resources."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from sourcemind.schemas.common import ItemList

ConnectorTypeLiteral = Literal["github", "discord", "slack", "notion"]
ConnectorStatusLiteral = Literal["active", "paused", "error"]
SyncTypeLiteral = Literal["full", "incremental"]
SyncStatusLiteral = Literal["running", "completed", "failed"]


# ─── Request schemas ──────────────────────────────────────────────────────────

class ConnectorCreateRequest(BaseModel):
    """Payload for creating a new connector configuration."""

    connector_type: ConnectorTypeLiteral = Field(description="e.g. 'github' or 'discord'")
    display_name: str = Field(min_length=1, max_length=255)
    config: dict[str, Any] = Field(
        default_factory=dict, description="Connector-specific settings"
    )


class ConnectorUpdateRequest(BaseModel):
    """Payload for updating a connector configuration."""

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    config: dict[str, Any] | None = Field(default=None)
    status: ConnectorStatusLiteral | None = Field(default=None)


class SyncTriggerRequest(BaseModel):
    """Payload for manually triggering a connector sync."""

    sync_type: SyncTypeLiteral = Field(
        default="incremental", description="'full' or 'incremental'"
    )


# ─── Response schemas ─────────────────────────────────────────────────────────

class ConnectorResponse(BaseModel):
    """Single connector configuration response."""

    id: UUID
    workspace_id: UUID
    connector_type: ConnectorTypeLiteral
    display_name: str
    config: dict[str, Any]
    status: ConnectorStatusLiteral
    last_sync_at: datetime | None
    next_sync_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ConnectorListResponse(ItemList[ConnectorResponse]):
    """List of connectors for a workspace."""


class SyncLogResponse(BaseModel):
    """Single sync log entry."""

    id: UUID
    connector_id: UUID
    sync_type: SyncTypeLiteral
    status: SyncStatusLiteral
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
    sync_type: SyncTypeLiteral
    message: str


class SyncLogsResponse(ItemList[SyncLogResponse]):
    """Paginated sync logs for a connector."""
