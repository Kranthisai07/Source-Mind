"""ORM model for workspace invitations (D-065). The token itself is never stored, only its hash."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CHAR, CheckConstraint, DateTime, ForeignKey, Index, Text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from sourcemind.models.base import Base


class WorkspaceInvitation(Base):
    """An invitation for one email address to join one workspace in one role."""

    __tablename__ = "workspace_invitations"
    __table_args__ = (
        CheckConstraint(
            "role IN ('owner', 'admin', 'member', 'viewer')",
            name="ck_workspace_invitations_role",
        ),
        CheckConstraint(
            "email = lower(email) AND email ~ '^[ -~]+@[ -~]+$'",
            name="ck_workspace_invitations_email",
        ),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_workspace_invitations_hash"),
        CheckConstraint("expires_at > created_at", name="ck_workspace_invitations_expiry"),
        CheckConstraint(
            "NOT (accepted_at IS NOT NULL AND revoked_at IS NOT NULL)",
            name="ck_workspace_invitations_terminal",
        ),
        Index(
            "uq_workspace_invitations_pending",
            "workspace_id",
            "email",
            unique=True,
            postgresql_where=sql_text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
        Index("ix_workspace_invitations_workspace", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    invited_by: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
