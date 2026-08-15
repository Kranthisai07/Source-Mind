"""ORM models for connector infrastructure."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sourcemind.models.base import Base, TimestampMixin


class ConnectorConfig(Base):
    """Persisted connector configuration for a workspace."""

    __tablename__ = "connector_configs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "connector_type", "display_name",
                         name="uq_connector_configs_ws_type_name"),
        {"comment": "Connector configuration scoped to a workspace."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default="gen_random_uuid()",
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    connector_type: Mapped[str] = mapped_column(Text(), nullable=False)
    display_name: Mapped[str] = mapped_column(Text(), nullable=False)
    config: Mapped[dict] = mapped_column(
        JSONB(), nullable=False, server_default="'{}'",
    )
    status: Mapped[str] = mapped_column(
        Text(), nullable=False, server_default="active",
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()",
    )

    sync_logs: Mapped[list["ConnectorSyncLog"]] = relationship(
        "ConnectorSyncLog", back_populates="connector", lazy="noload",
    )

    def __repr__(self) -> str:
        return f"<ConnectorConfig {self.connector_type}:{self.display_name}>"


class ConnectorSyncLog(Base):
    """Record of a single sync run for a connector."""

    __tablename__ = "connector_sync_logs"
    __table_args__ = (
        {"comment": "Audit log of connector sync runs."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default="gen_random_uuid()",
    )
    connector_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("connector_configs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    sync_type: Mapped[str] = mapped_column(Text(), nullable=False)
    status: Mapped[str] = mapped_column(Text(), nullable=False)
    artifacts_found: Mapped[int] = mapped_column(
        Integer(), nullable=False, server_default="0",
    )
    artifacts_new: Mapped[int] = mapped_column(
        Integer(), nullable=False, server_default="0",
    )
    artifacts_skipped: Mapped[int] = mapped_column(
        Integer(), nullable=False, server_default="0",
    )
    error_message: Mapped[str | None] = mapped_column(Text(), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()",
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    connector: Mapped["ConnectorConfig"] = relationship(
        "ConnectorConfig", back_populates="sync_logs", lazy="noload",
    )

    def __repr__(self) -> str:
        return f"<ConnectorSyncLog {self.sync_type}:{self.status}>"


class ArtifactLink(Base):
    """Links an ingested memory back to its original source artifact."""

    __tablename__ = "artifact_links"
    __table_args__ = (
        # One row per (artifact, memory) — a document yields many memories.
        # See migration 20250816_0004 for the full rationale.
        UniqueConstraint("workspace_id", "source_tool", "source_type", "source_id",
                         "memory_id",
                         name="uq_artifact_links_ws_tool_type_id_memory"),
        {"comment": "Maps memories to their originating external artifacts."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default="gen_random_uuid()",
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=True, index=True,
        comment="Anchor written at sync time, before memories exist.",
    )
    memory_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("memories.id", ondelete="CASCADE"),
        nullable=True, index=True,
        comment="Backfilled once the ingestion pipeline produces memories.",
    )
    source_tool: Mapped[str] = mapped_column(Text(), nullable=False)
    source_type: Mapped[str] = mapped_column(Text(), nullable=False)
    source_id: Mapped[str] = mapped_column(Text(), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text(), nullable=True)
    source_author: Mapped[str | None] = mapped_column(Text(), nullable=True)
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    identity_confidence: Mapped[float | None] = mapped_column(nullable=True)
    artifact_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB(), nullable=False, server_default="'{}'",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()",
    )

    def __repr__(self) -> str:
        return f"<ArtifactLink {self.source_tool}:{self.source_type}:{self.source_id}>"
