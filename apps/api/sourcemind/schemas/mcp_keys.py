"""Request and response schemas for workspace MCP API keys."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class McpKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    expires_in_days: Literal[30, 90, 365] = 90

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value:
            raise ValueError("name must not be blank")
        return value


class McpKeyCreated(BaseModel):
    id: UUID
    name: str
    key: str = Field(repr=False)
    key_prefix: str
    expires_at: datetime
    created_at: datetime


class McpKeyOut(BaseModel):
    id: UUID
    name: str
    key_prefix: str
    user_id: UUID
    creator_display_name: str
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    status: Literal["active", "expired", "revoked"]


class McpKeyList(BaseModel):
    keys: list[McpKeyOut]
    mcp_enabled: bool
