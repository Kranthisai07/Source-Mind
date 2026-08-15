"""Document model — raw source material before fact extraction."""

import uuid
from enum import StrEnum

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sourcemind.models.base import Base, SoftDeleteMixin, TimestampMixin


class DocumentSourceType(StrEnum):
    """Supported input source types for the ingestion pipeline."""

    URL = "url"
    PDF = "pdf"
    DOCX = "docx"
    CODE = "code"
    TEXT = "text"
    GITHUB = "github"
    SLACK = "slack"
    NOTION = "notion"


class IngestionStatus(StrEnum):
    """Pipeline processing state for a document."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DUPLICATE = "duplicate"  # SHA-256 matched existing document


class Document(Base, TimestampMixin, SoftDeleteMixin):
    """
    Raw source document submitted for ingestion.

    Documents pass through the 7-stage pipeline:
    RECEIVE → EXTRACT → CHUNK → EXTRACT FACTS → EMBED → ATTRIBUTE → INDEX

    The SHA-256 hash enables content-addressed deduplication within a workspace.
    A document that matches an existing SHA-256 is marked DUPLICATE and not reprocessed.
    """

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "sha256_hash",
            name="uq_documents_workspace_sha256",
        ),
        {"comment": "Source document submitted for memory extraction. One document → N memories."},
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
    submitter_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Document title — extracted or provided by submitter",
    )
    source_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="DocumentSourceType enum value",
    )
    source_url: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
        comment="Original URL if source_type=url",
    )
    sha256_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="SHA-256 hex digest of normalized content for deduplication",
    )
    s3_key: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        comment="S3 object key for the archived raw document",
    )
    ingestion_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default="pending",
        index=True,
        comment="Current pipeline stage: IngestionStatus enum value",
    )
    ingestion_job_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        comment="Celery task ID for job status polling",
    )
    error_message: Mapped[str | None] = mapped_column(
        nullable=True,
        comment="Last error message if ingestion_status=failed",
    )
    chunk_count: Mapped[int | None] = mapped_column(
        nullable=True,
        comment="Number of chunks produced during Stage 3",
    )
    memory_count: Mapped[int | None] = mapped_column(
        nullable=True,
        comment="Number of memories extracted during Stage 4",
    )
    pipeline_data: Mapped[dict] = mapped_column(
        # The DB column is `metadata` (migration 0001); the ORM attribute is
        # renamed because SQLAlchemy's Declarative API reserves `metadata`.
        # Same pattern as User.clerk_data and ArtifactLink.artifact_metadata.
        "metadata",
        nullable=False,
        server_default="'{}'::jsonb",
        comment="Pipeline metrics: token count, processing times, stage durations, etc.",
    )

    # ── Relationships ─────────────────────────────────────────────
    workspace: Mapped["Workspace"] = relationship(  # type: ignore[name-defined]
        "Workspace",
        back_populates="documents",
        lazy="noload",
    )
    submitter: Mapped["User"] = relationship(  # type: ignore[name-defined]
        "User",
        back_populates="documents_submitted",
        lazy="noload",
    )
    memories: Mapped[list["Memory"]] = relationship(  # type: ignore[name-defined]
        "Memory",
        back_populates="document",
        lazy="noload",
    )

    def __repr__(self) -> str:
        return (
            f"<Document id={self.id} source_type={self.source_type!r} "
            f"status={self.ingestion_status!r}>"
        )
