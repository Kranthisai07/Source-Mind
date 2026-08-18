"""MemoryConflict model — detected contradictions and duplicates between memories."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sourcemind.models.base import Base, TimestampMixin


class ConflictType(StrEnum):
    """
    Category of knowledge conflict.

    contradiction — two memories assert opposing facts
    duplication   — two memories assert the same fact (near-duplicate)
    ambiguity     — two memories are similar enough to be confusing but not duplicates
    """

    CONTRADICTION = "contradiction"
    DUPLICATION = "duplication"
    AMBIGUITY = "ambiguity"


class ConflictSeverity(StrEnum):
    """How urgently the conflict should be resolved."""

    LOW = "low"
    MEDIUM = "medium"
    CRITICAL = "critical"


class ConflictStatus(StrEnum):
    """Current resolution state of a detected conflict."""

    OPEN = "open"
    UNDER_REVIEW = "under_review"  # Admin has acknowledged and opened it
    RESOLVED = "resolved"
    DEFERRED = "deferred"          # Explicitly postponed with revisit date
    DISMISSED = "dismissed"        # Acknowledged as intentional or non-issue


class MemoryConflict(Base, TimestampMixin):
    """
    A detected conflict between two memories in the same workspace.

    Conflicts are detected during Stage 7 (INDEX & RELATE) when the cosine
    similarity between two memories exceeds the contradiction threshold (0.85).
    The LLM classifier determines conflict_type and severity.

    Teams can resolve conflicts via the API:
      POST /v1/memories/:id/conflicts/:conflict_id/resolve
    Resolution is recorded with the resolver's identity for full audit trail.
    """

    __tablename__ = "memory_conflicts"
    __table_args__ = {
        "comment": "Detected knowledge conflicts between memories. Resolved via team review.",
    }

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
        comment="Workspace containing both memories",
    )
    memory_a_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("memories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    memory_b_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("memories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conflict_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="ConflictType enum value",
    )
    severity: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default="'low'",
        comment="ConflictSeverity enum value; computed by compute_conflict_severity()",
    )
    competing_claim_count: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        server_default="2",
        comment=(
            "Distinct memories disputing the same memory, counted across all "
            "of its conflict rows. A pairwise conflict alone is 2."
        ),
    )
    blocks_derivation: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        server_default="false",
        comment="True only while severity='critical' and the conflict is unresolved",
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default="'open'",
        index=True,
        comment="ConflictStatus enum value",
    )
    similarity_score: Mapped[float] = mapped_column(
        nullable=False,
        comment="Cosine similarity that triggered conflict detection [0.0–1.0]",
    )
    explanation: Mapped[str | None] = mapped_column(
        nullable=True,
        comment="LLM-generated explanation of the conflict",
    )
    resolver_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User who resolved or dismissed this conflict",
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        comment="Timestamp of resolution",
    )
    resolution_note: Mapped[str | None] = mapped_column(
        nullable=True,
        comment="Free-text explanation of how the conflict was resolved",
    )

    # ── Relationships ─────────────────────────────────────────────
    memory_a: Mapped["Memory"] = relationship(  # type: ignore[name-defined]
        "Memory",
        foreign_keys=[memory_a_id],
        lazy="noload",
    )
    memory_b: Mapped["Memory"] = relationship(  # type: ignore[name-defined]
        "Memory",
        foreign_keys=[memory_b_id],
        lazy="noload",
    )
    resolver: Mapped["User | None"] = relationship(  # type: ignore[name-defined]
        "User",
        foreign_keys=[resolver_id],
        lazy="noload",
    )

    def __repr__(self) -> str:
        return (
            f"<MemoryConflict id={self.id} type={self.conflict_type!r} "
            f"status={self.status!r} similarity={self.similarity_score:.3f}>"
        )
