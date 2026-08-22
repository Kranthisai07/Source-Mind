"""
SQLAlchemy declarative base and shared mixins.

All models inherit from Base. Shared audit fields are provided via mixins
rather than repeated on every table.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """
    Declarative base for all SourceMind ORM models.

    Uses PostgreSQL-native UUID type for all primary keys.
    The type_annotation_map registers Python types → SQLAlchemy column types
    so that Mapped[dict] resolves to JSONB automatically.
    """

    type_annotation_map: dict[Any, Any] = {
        dict: JSONB,
        dict[str, Any]: JSONB,
    }


class TimestampMixin:
    """
    Adds created_at and updated_at columns to any model.

    updated_at is automatically maintained by PostgreSQL trigger
    via the set_updated_at() function created in the initial migration.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        onupdate=func.now(),
        nullable=True,
    )


class SoftDeleteMixin:
    """
    Adds soft-delete support via deleted_at timestamp.

    deleted_at=NULL means the record is active.
    deleted_at=<timestamp> means soft-deleted (invisible to normal queries).

    Application code must filter: WHERE deleted_at IS NULL
    for all user-facing queries. RLS policies enforce this at the DB level.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    @property
    def is_deleted(self) -> bool:
        """True if this record has been soft-deleted."""
        return self.deleted_at is not None
