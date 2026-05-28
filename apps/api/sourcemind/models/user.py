"""User model — linked to Clerk for authentication."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sourcemind.models.base import Base, SoftDeleteMixin, TimestampMixin


class User(Base, TimestampMixin, SoftDeleteMixin):
    """
    SourceMind user record. Authentication is delegated to Clerk.

    The clerk_id is the external identifier from Clerk's auth system.
    Internal operations use the UUID id for all foreign key relationships.

    A user can belong to multiple workspaces across organizations.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("clerk_id", name="uq_users_clerk_id"),
        UniqueConstraint("email", name="uq_users_email"),
        {"comment": "Platform user, authenticated via Clerk."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default="gen_random_uuid()",
        comment="Internal user UUID",
    )
    clerk_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
        comment="Clerk user ID (e.g. user_2abc...)",
    )
    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        unique=True,
        index=True,
        comment="Primary email address",
    )
    display_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="User's preferred display name",
    )
    avatar_url: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
        comment="Profile picture URL",
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of last API request",
    )
    clerk_data: Mapped[dict] = mapped_column(
        "metadata",
        nullable=False,
        server_default="'{}'::jsonb",
        comment=(
            "Additional profile data synced from Clerk. ORM attribute is "
            "`clerk_data` (since SQLAlchemy reserves `metadata`); the actual "
            "DB column is `metadata` per migration 0001."
        ),
    )

    # ── Relationships ─────────────────────────────────────────────
    workspace_memberships: Mapped[list["WorkspaceMember"]] = relationship(  # type: ignore[name-defined]
        "WorkspaceMember",
        foreign_keys="WorkspaceMember.user_id",
        back_populates="user",
        lazy="noload",
    )
    documents_submitted: Mapped[list["Document"]] = relationship(  # type: ignore[name-defined]
        "Document",
        back_populates="submitter",
        lazy="noload",
    )
    attributions: Mapped[list["Attribution"]] = relationship(  # type: ignore[name-defined]
        "Attribution",
        back_populates="user",
        lazy="noload",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"
