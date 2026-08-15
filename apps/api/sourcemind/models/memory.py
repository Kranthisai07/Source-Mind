"""
Memory model — the core atomic unit of knowledge in SourceMind.

A memory is a single, self-contained fact extracted from a document.
It carries a full embedding vector for semantic search, versioning metadata,
and links to attribution records.
"""

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sourcemind.models.base import Base, SoftDeleteMixin, TimestampMixin


class Memory(Base, TimestampMixin, SoftDeleteMixin):
    """
    A single atomic fact extracted from source material.

    Design principles:
    - Self-contained: readable without surrounding context (pronouns resolved,
      relative dates grounded, no implicit references)
    - Versioned: every edit creates a new version record; current_version=True
      marks the active version
    - Attributed: attribution records link contributors to this memory
    - Embedded: vector(3072) enables semantic similarity search via HNSW

    Versioning strategy:
      On PATCH /v1/memories/:id, the service:
      1. Sets current_version=False on the existing record
      2. Inserts a new record with version=N+1, current_version=True
      3. Copies the parent_memory_id to maintain the version chain
      This preserves the full edit history without UPDATE tombstones.
    """

    __tablename__ = "memories"
    __table_args__ = (
        Index(
            "ix_memories_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_memories_workspace_current", "workspace_id", "current_version"),
        Index("ix_memories_document", "document_id"),
        Index(
            "ix_memories_content_fts",
            "content",
            postgresql_using="gin",
            postgresql_ops={"content": "gin_trgm_ops"},
        ),
        {"comment": "Atomic unit of knowledge extracted from a document."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default="gen_random_uuid()",
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        comment="Source document, if extracted from a document",
    )
    parent_memory_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("memories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Previous version's ID for version chain traversal",
    )

    # ── Content ───────────────────────────────────────────────────
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="The full text of this atomic fact",
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(3072),
        nullable=True,
        comment="OpenAI text-embedding-3-large (3072-dim) for semantic search",
    )
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="SHA-256 of content for embedding cache lookup",
    )

    # ── Versioning ────────────────────────────────────────────────
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="1",
        comment="Monotonically increasing version number within a memory chain",
    )
    current_version: Mapped[bool] = mapped_column(
        nullable=False,
        server_default="true",
        comment="True for the latest version in the chain",
    )

    # ── Classification ────────────────────────────────────────────
    tags: Mapped[list[str] | None] = mapped_column(
        ARRAY(String),
        nullable=True,
        comment="Free-form tags for filtering and organization",
    )
    category: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Classifier category: decision | fact | process | question | definition",
    )
    source_chunk_index: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Which chunk in the source document produced this memory",
    )
    confidence_score: Mapped[float | None] = mapped_column(
        nullable=True,
        comment="LLM confidence in fact extraction (0.0–1.0)",
    )
    importance_score: Mapped[float] = mapped_column(
        nullable=False,
        server_default="0.0",
        comment="Computed importance weight [0.0–1.0]; recalculated on edit, relation creation, conflict resolution, and handoff",
    )

    # ── Relationships ─────────────────────────────────────────────
    workspace: Mapped["Workspace"] = relationship(  # type: ignore[name-defined]
        "Workspace",
        back_populates="memories",
        lazy="noload",
    )
    document: Mapped["Document | None"] = relationship(  # type: ignore[name-defined]
        "Document",
        back_populates="memories",
        lazy="noload",
    )
    parent_memory: Mapped["Memory | None"] = relationship(
        "Memory",
        remote_side="Memory.id",
        foreign_keys=[parent_memory_id],
        lazy="noload",
    )
    attributions: Mapped[list["Attribution"]] = relationship(  # type: ignore[name-defined]
        "Attribution",
        back_populates="memory",
        lazy="noload",
    )
    attribution_edits: Mapped[list["AttributionEdit"]] = relationship(  # type: ignore[name-defined]
        "AttributionEdit",
        back_populates="memory",
        lazy="noload",
    )
    outgoing_relations: Mapped[list["MemoryRelation"]] = relationship(  # type: ignore[name-defined]
        "MemoryRelation",
        foreign_keys="MemoryRelation.source_memory_id",
        back_populates="source_memory",
        lazy="noload",
    )
    incoming_relations: Mapped[list["MemoryRelation"]] = relationship(  # type: ignore[name-defined]
        "MemoryRelation",
        foreign_keys="MemoryRelation.target_memory_id",
        back_populates="target_memory",
        lazy="noload",
    )

    def __repr__(self) -> str:
        preview = self.content[:60] + "..." if len(self.content) > 60 else self.content
        return f"<Memory id={self.id} v={self.version} current={self.current_version} content={preview!r}>"
