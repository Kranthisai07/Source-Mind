"""Fix ArtifactLink FK timing: anchor links on document_id, backfill memory_id.

Revision ID: 20250816_0004
Revises: 20250415_0003
Create Date: 2026-08-16

PROBLEM
-------
`GitHubConnector._ingest` ran at *sync* time and set `memory_id` to the
document's UUID. At that moment only a `documents` row exists — memories are
created later by the Celery pipeline — so the FK to `memories.id` was violated
on every single sync. The connector could never write a link.

FIX
---
Links are now anchored on `document_id` at sync time, and `memory_id` is
backfilled once ingestion produces memories.

CARDINALITY DECISION
--------------------
One document routinely yields many memories (the e2e test produces 7 from a
single paragraph). We create **one ArtifactLink row per resulting memory**, so
every memory can be traced to its originating artifact independently.

That breaks the old `uq_artifact_links_ws_tool_type_id` unique constraint,
which allowed only one row per artifact. It is replaced by two constraints
that together preserve the original dedup guarantee:

  * `uq_artifact_links_ws_tool_type_id_memory` — UNIQUE over
    (workspace_id, source_tool, source_type, source_id, memory_id).
    Permits N backfilled rows per artifact, one per memory, and still blocks
    the same memory being linked to the same artifact twice.

  * `uq_artifact_links_pending` — a PARTIAL unique index over
    (workspace_id, source_tool, source_type, source_id) WHERE memory_id IS NULL.
    Postgres treats NULLs as distinct in a normal UNIQUE, so without this a
    re-sync could insert unlimited un-backfilled anchor rows for one artifact.
    This caps it at exactly one pending anchor per artifact.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20250816_0004"
down_revision = "20250415_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── document_id: the anchor written at sync time ─────────────────────────
    op.add_column(
        "artifact_links",
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_artifact_links_document",
        "artifact_links",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_artifact_links_document", "artifact_links", ["document_id"]
    )

    # ── memory_id becomes nullable (backfilled post-ingestion) ───────────────
    op.alter_column(
        "artifact_links",
        "memory_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )

    # ── swap the artifact-level constraint for the memory-level one ──────────
    op.drop_constraint(
        "uq_artifact_links_ws_tool_type_id", "artifact_links", type_="unique"
    )
    op.create_unique_constraint(
        "uq_artifact_links_ws_tool_type_id_memory",
        "artifact_links",
        ["workspace_id", "source_tool", "source_type", "source_id", "memory_id"],
    )
    op.create_index(
        "uq_artifact_links_pending",
        "artifact_links",
        ["workspace_id", "source_tool", "source_type", "source_id"],
        unique=True,
        postgresql_where=sa.text("memory_id IS NULL"),
    )


def downgrade() -> None:
    """Reverse the changes.

    OPERATOR WARNING — restoring NOT NULL on `memory_id` will FAIL if any row
    still has memory_id NULL (i.e. an artifact whose ingestion never completed,
    or one that is mid-flight). Before downgrading, either backfill those rows
    or delete them:

        DELETE FROM artifact_links WHERE memory_id IS NULL;

    Collapsing back to the artifact-level unique constraint will ALSO fail if
    any artifact has more than one linked memory — which is the normal state
    after this migration. Deduplicate first, keeping one row per artifact:

        DELETE FROM artifact_links a USING artifact_links b
        WHERE a.ctid < b.ctid
          AND a.workspace_id = b.workspace_id
          AND a.source_tool  = b.source_tool
          AND a.source_type  = b.source_type
          AND a.source_id    = b.source_id;
    """
    op.drop_index("uq_artifact_links_pending", table_name="artifact_links")
    op.drop_constraint(
        "uq_artifact_links_ws_tool_type_id_memory",
        "artifact_links",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_artifact_links_ws_tool_type_id",
        "artifact_links",
        ["workspace_id", "source_tool", "source_type", "source_id"],
    )

    op.alter_column(
        "artifact_links",
        "memory_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )

    op.drop_index("ix_artifact_links_document", table_name="artifact_links")
    op.drop_constraint(
        "fk_artifact_links_document", "artifact_links", type_="foreignkey"
    )
    op.drop_column("artifact_links", "document_id")
