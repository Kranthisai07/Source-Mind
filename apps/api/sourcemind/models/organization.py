"""Organization model — top-level tenant boundary."""

import uuid
from enum import StrEnum

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sourcemind.models.base import Base, SoftDeleteMixin, TimestampMixin


class OrganizationPlan(StrEnum):
    """Billing / feature plan tiers."""

    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class Organization(Base, TimestampMixin, SoftDeleteMixin):
    """
    Root tenant entity. Every workspace belongs to exactly one organization.

    Isolation level: all data is scoped to organization → workspace.
    Billing and seat limits are applied at the organization level.
    """

    __tablename__ = "organizations"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_organizations_slug"),
        {"comment": "Top-level tenant. All workspaces and users belong to an organization."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default="gen_random_uuid()",
        comment="Unique organization identifier",
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Display name shown in the UI",
    )
    slug: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        index=True,
        comment="URL-safe unique identifier, e.g. 'acme-corp'",
    )
    plan: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default="free",
        comment="Billing plan: free | pro | enterprise",
    )
    max_workspaces: Mapped[int] = mapped_column(
        nullable=False,
        server_default="3",
        comment="Maximum number of workspaces allowed on this plan",
    )
    max_members: Mapped[int] = mapped_column(
        nullable=False,
        server_default="10",
        comment="Maximum team members across all workspaces",
    )
    settings: Mapped[dict] = mapped_column(
        nullable=False,
        server_default="'{}'::jsonb",
        comment="Organization-level feature flags and configuration",
    )

    # ── Relationships ─────────────────────────────────────────────
    workspaces: Mapped[list["Workspace"]] = relationship(  # type: ignore[name-defined]
        "Workspace",
        back_populates="organization",
        lazy="noload",
    )

    def __repr__(self) -> str:
        return f"<Organization id={self.id} slug={self.slug!r} plan={self.plan!r}>"
