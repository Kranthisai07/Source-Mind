"""
Attribution and AttributionEdit models.

Attribution records are APPEND-ONLY — enforced by a PostgreSQL trigger
in the initial migration. No UPDATE or DELETE is permitted.

This guarantees a tamper-proof provenance chain for every memory.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sourcemind.models.base import Base, TimestampMixin


class AttributionActionType(StrEnum):
    """
    Type of action that triggered an attribution record.

    create  — initial memory creation (submitter gets 100%)
    edit    — content modification (triggers 5-signal recompute)
    approve — explicit approval/review action (10% weight)
    reject  — rejection (recorded but does not grant weight)
    merge   — accepted a suggested edit
    """

    CREATE = "create"
    EDIT = "edit"
    APPROVE = "approve"
    REJECT = "reject"
    MERGE = "merge"


class Attribution(Base):
    """
    Attribution record — who owns how much of a memory at a point in time.

    Records are computed after every edit and stored as a snapshot.
    The most recent record per (memory_id, user_id) pair is the current weight.

    APPEND-ONLY: the PostgreSQL trigger `attribution_append_only` prevents
    any UPDATE or DELETE on this table. Enforced at the database level,
    not just application level. Because UPDATE is impossible, we do not
    inherit TimestampMixin (no `updated_at` column exists on this table).

    Weight semantics:
      contribution_weight: normalized share of total attribution (0.0–1.0)
      All weights for a given memory_id (latest per user) sum to 1.0.
      Floor: minimum 0.02 for any contributor with ≥1 substantive edit.
    """

    __tablename__ = "attributions"
    __table_args__ = {
        "comment": (
            "Append-only attribution snapshot. Enforced by DB trigger. "
            "Latest record per (memory_id, user_id) = current attribution."
        ),
    }

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default="gen_random_uuid()",
    )
    memory_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("memories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Aggregated score ──────────────────────────────────────────
    contribution_weight: Mapped[float] = mapped_column(
        nullable=False,
        comment="Normalized contribution share [0.0–1.0]. All contributors sum to 1.0.",
    )

    # ── Signal breakdown (for audit/debugging) ────────────────────
    char_diff_score: Mapped[float | None] = mapped_column(
        nullable=True,
        comment="Signal 1: character diff score [0.0–1.0], weight=35%",
    )
    semantic_score: Mapped[float | None] = mapped_column(
        nullable=True,
        comment="Signal 2: SBERT semantic similarity [0.0–1.0], weight=30%",
    )
    temporal_score: Mapped[float | None] = mapped_column(
        nullable=True,
        comment="Signal 3: temporal primacy score [0.0–1.0], weight=15%",
    )
    structural_score: Mapped[float | None] = mapped_column(
        nullable=True,
        comment="Signal 4: new named entities [0.0–1.0], weight=10%",
    )
    approval_score: Mapped[float | None] = mapped_column(
        nullable=True,
        comment="Signal 5: explicit approval [0.0 or 0.1], weight=10%",
    )

    # ── Context ───────────────────────────────────────────────────
    trigger_action: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="AttributionActionType that triggered this computation",
    )
    edit_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("attribution_edits.id", ondelete="SET NULL"),
        nullable=True,
        comment="The edit that caused this recomputation",
    )

    # ── Timestamps (created_at only — table is append-only) ───────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
        comment="Computation time of this attribution snapshot",
    )

    # ── Relationships ─────────────────────────────────────────────
    memory: Mapped["Memory"] = relationship(  # type: ignore[name-defined]
        "Memory",
        back_populates="attributions",
        lazy="noload",
    )
    user: Mapped["User"] = relationship(  # type: ignore[name-defined]
        "User",
        back_populates="attributions",
        lazy="noload",
    )

    def __repr__(self) -> str:
        return (
            f"<Attribution memory={self.memory_id} user={self.user_id} "
            f"weight={self.contribution_weight:.3f}>"
        )


class AttributionEdit(Base, TimestampMixin):
    """
    Record of a single content edit to a memory.

    Stores the before/after content for diff computation.
    Used as input to the 5-signal attribution scoring engine.

    These records are also effectively append-only (no UPDATE),
    though the DB trigger only covers the attributions table.
    Application code must never UPDATE attribution_edits.
    """

    __tablename__ = "attribution_edits"
    __table_args__ = {
        "comment": "Individual content edit records used for attribution computation.",
    }

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default="gen_random_uuid()",
    )
    memory_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("memories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    editor_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    content_before: Mapped[str | None] = mapped_column(
        nullable=True,
        comment="Content prior to this edit. NULL for initial creation.",
    )
    content_after: Mapped[str] = mapped_column(
        nullable=False,
        comment="Content after this edit.",
    )
    edit_position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="1-indexed position of this edit in the edit history for this memory",
    )
    action_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="AttributionActionType value",
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        unique=True,
        comment="UUID v4 idempotency key from the request that triggered this edit",
    )

    # ── Relationships ─────────────────────────────────────────────
    memory: Mapped["Memory"] = relationship(  # type: ignore[name-defined]
        "Memory",
        back_populates="attribution_edits",
        lazy="noload",
    )
    editor: Mapped["User"] = relationship(  # type: ignore[name-defined]
        "User",
        lazy="noload",
    )

    def __repr__(self) -> str:
        return (
            f"<AttributionEdit memory={self.memory_id} editor={self.editor_id} "
            f"pos={self.edit_position} action={self.action_type!r}>"
        )
