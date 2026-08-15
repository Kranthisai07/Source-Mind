"""Workspace and WorkspaceMember models."""

import uuid
from enum import StrEnum

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sourcemind.models.base import Base, SoftDeleteMixin, TimestampMixin


class WorkspaceRole(StrEnum):
    """
    Role hierarchy within a workspace.

    owner   — full control, can delete workspace, manage billing
    admin   — manage members, configure integrations
    member  — create and edit memories, view all content
    viewer  — read-only access to summaries
    """

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class Workspace(Base, TimestampMixin, SoftDeleteMixin):
    """
    Isolated collaboration space within an organization.

    All memories, documents, and attributions are scoped to a workspace.
    Row-level security policies enforce workspace isolation at the DB level.
    """

    __tablename__ = "workspaces"
    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_workspaces_org_slug"),
        {"comment": "Isolated team collaboration space. All content is scoped to a workspace."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default="gen_random_uuid()",
        comment="Unique workspace identifier",
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Parent organization",
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Display name, e.g. 'Engineering Team'",
    )
    slug: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="URL-safe identifier, unique within organization",
    )
    description: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )
    settings: Mapped[dict] = mapped_column(
        nullable=False,
        server_default="'{}'::jsonb",
        comment="Workspace-level feature flags and configuration",
    )

    # ── Relationships ─────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(  # type: ignore[name-defined]
        "Organization",
        back_populates="workspaces",
        lazy="noload",
    )
    members: Mapped[list["WorkspaceMember"]] = relationship(
        "WorkspaceMember",
        back_populates="workspace",
        lazy="noload",
    )
    documents: Mapped[list["Document"]] = relationship(  # type: ignore[name-defined]
        "Document",
        back_populates="workspace",
        lazy="noload",
    )
    memories: Mapped[list["Memory"]] = relationship(  # type: ignore[name-defined]
        "Memory",
        back_populates="workspace",
        lazy="noload",
    )

    def __repr__(self) -> str:
        return f"<Workspace id={self.id} slug={self.slug!r}>"


class WorkspaceMember(Base, TimestampMixin):
    """
    Join table linking users to workspaces with a role.

    A user can be a member of multiple workspaces across organizations.
    The role determines what operations they can perform.
    """

    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_members_workspace_user"),
        {"comment": "Workspace membership with role-based access control."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default="gen_random_uuid()",
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default="member",
        comment="WorkspaceRole enum value",
    )
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Relationships ─────────────────────────────────────────────
    workspace: Mapped["Workspace"] = relationship(
        "Workspace",
        back_populates="members",
        lazy="noload",
    )
    user: Mapped["User"] = relationship(  # type: ignore[name-defined]
        "User",
        foreign_keys=[user_id],
        back_populates="workspace_memberships",
        lazy="noload",
    )

    def __repr__(self) -> str:
        return f"<WorkspaceMember user={self.user_id} workspace={self.workspace_id} role={self.role!r}>"
