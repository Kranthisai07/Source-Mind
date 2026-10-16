"""ORM models for workspace-scoped MCP API keys."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CHAR, BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from sourcemind.models.base import Base


class McpApiKey(Base):
    """Hashed MCP credential bound to one creator and one workspace."""

    __tablename__ = "mcp_api_keys"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="ck_mcp_api_keys_expiry"),
        CheckConstraint("key_hash ~ '^[0-9a-f]{64}$'", name="ck_mcp_api_keys_hash"),
        Index("ix_mcp_api_keys_workspace_user", "workspace_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    key_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class McpKeyEvent(Base):
    """Append-only audit event for MCP key creation and revocation."""

    __tablename__ = "mcp_key_events"
    __table_args__ = (
        CheckConstraint("event IN ('created', 'revoked')", name="ck_mcp_key_events_event"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
