"""MemoryRelation model — typed semantic relationships between memories."""

import uuid
from enum import StrEnum

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sourcemind.models.base import Base, TimestampMixin


class RelationType(StrEnum):
    """
    Semantic relationship types between memory nodes.

    updates     — source is a newer version/correction of target
    extends     — source adds to or expands upon target
    derives     — source was logically derived from target
    contradicts — source directly conflicts with target (triggers conflict detection)
    references  — source mentions or cites target
    """

    UPDATES = "updates"
    EXTENDS = "extends"
    DERIVES = "derives"
    CONTRADICTS = "contradicts"
    REFERENCES = "references"


class MemoryRelation(Base, TimestampMixin):
    """
    Directed relationship between two memory nodes.

    Relations are detected automatically during Stage 7 of the ingestion pipeline
    when cosine similarity > threshold for different relation types.
    They are also mirrored in Neo4j for efficient graph traversal.

    Direction: source_memory_id → [relation_type] → target_memory_id
    """

    __tablename__ = "memory_relations"
    __table_args__ = (
        UniqueConstraint(
            "source_memory_id", "target_memory_id", "relation_type",
            name="uq_memory_relations_unique_edge",
        ),
        {"comment": "Directed semantic relationship between two memories."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default="gen_random_uuid()",
    )
    source_memory_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("memories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_memory_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("memories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relation_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="RelationType enum value",
    )
    similarity_score: Mapped[float | None] = mapped_column(
        nullable=True,
        comment="Cosine similarity that triggered this relation detection [0.0–1.0]",
    )
    confidence: Mapped[float | None] = mapped_column(
        nullable=True,
        comment="Classifier confidence in this relation type [0.0–1.0]",
    )
    detected_by: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default="'pipeline'",
        comment="Who created this relation: pipeline | user | llm",
    )

    # ── Relationships ─────────────────────────────────────────────
    source_memory: Mapped["Memory"] = relationship(  # type: ignore[name-defined]
        "Memory",
        foreign_keys=[source_memory_id],
        back_populates="outgoing_relations",
        lazy="noload",
    )
    target_memory: Mapped["Memory"] = relationship(  # type: ignore[name-defined]
        "Memory",
        foreign_keys=[target_memory_id],
        back_populates="incoming_relations",
        lazy="noload",
    )

    def __repr__(self) -> str:
        return (
            f"<MemoryRelation {self.source_memory_id} "
            f"--[{self.relation_type}]--> {self.target_memory_id}>"
        )
