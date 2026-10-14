"""Source provenance and stale-source markers (additive).

Revision ID: 20261014_0012
Revises: 20261010_0011
Create Date: 2026-10-14

All changes are additive and nullable/defaulted, so the previous API and worker
keep working against the migrated schema (rolling-release order: migrate,
worker, API). This migration is never downgraded in production; the downgrade
exists for local round-trip tests and refuses to run once any row uses the new
columns.

* ``artifact_links.source_revision`` / ``revision_seq``: which revision of a
  source artifact a link describes (content digest and a per-artifact counter).
* ``artifact_links.connector_id``: the connector that produced the link (no
  foreign key: a connector may be deleted while its provenance must remain).
* ``memories.source_stale_at`` / ``source_stale_reason``: a human-edited memory
  whose source later changed or disappeared stays searchable and is flagged
  instead of hidden.
* ``memories.deleted_reason``: why a machine memory was retired.

No new table is created, so no new RLS policy or runtime grant is needed:
``artifact_links`` and ``memories`` already have FORCE RLS and table-level
runtime privileges, which cover added columns.
"""

from __future__ import annotations

from alembic import op

revision = "20261014_0012"
down_revision = "20261010_0011"
branch_labels = None
depends_on = None

STALE_REASONS = "('source_revised','source_deleted','connector_removed')"
DELETED_REASONS = (
    "('user_deleted','source_superseded','source_deleted','connector_removed')"
)


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE artifact_links
            ADD COLUMN IF NOT EXISTS source_revision text,
            ADD COLUMN IF NOT EXISTS revision_seq integer NOT NULL DEFAULT 1,
            ADD COLUMN IF NOT EXISTS connector_id uuid
        """
    )
    op.execute(
        f"""
        ALTER TABLE memories
            ADD COLUMN IF NOT EXISTS source_stale_at timestamptz,
            ADD COLUMN IF NOT EXISTS source_stale_reason text,
            ADD COLUMN IF NOT EXISTS deleted_reason text,
            ADD CONSTRAINT ck_memories_source_stale_reason CHECK (
                source_stale_reason IS NULL
                OR source_stale_reason IN {STALE_REASONS}
            ),
            ADD CONSTRAINT ck_memories_source_stale_pair CHECK (
                (source_stale_at IS NULL) = (source_stale_reason IS NULL)
            ),
            ADD CONSTRAINT ck_memories_deleted_reason CHECK (
                deleted_reason IS NULL
                OR deleted_reason IN {DELETED_REASONS}
            )
        """
    )
    # Latest-revision lookup for one artifact (provenance and stale links).
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_artifact_links_identity_revision
            ON artifact_links
               (workspace_id, source_tool, source_type, source_id, revision_seq DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_artifact_links_connector
            ON artifact_links (connector_id)
            WHERE connector_id IS NOT NULL
        """
    )


def downgrade() -> None:
    conn = op.get_bind()
    used = conn.exec_driver_sql(
        """
        SELECT
            (SELECT count(*) FROM memories
              WHERE source_stale_at IS NOT NULL OR deleted_reason IS NOT NULL)
          + (SELECT count(*) FROM artifact_links
              WHERE source_revision IS NOT NULL OR connector_id IS NOT NULL
                 OR revision_seq <> 1)
        """
    ).scalar()
    if used:
        raise RuntimeError(
            "Refusing to downgrade 20261014_0012: provenance columns are in use."
        )
    op.execute("DROP INDEX IF EXISTS ix_artifact_links_connector")
    op.execute("DROP INDEX IF EXISTS ix_artifact_links_identity_revision")
    op.execute(
        """
        ALTER TABLE memories
            DROP CONSTRAINT IF EXISTS ck_memories_deleted_reason,
            DROP CONSTRAINT IF EXISTS ck_memories_source_stale_pair,
            DROP CONSTRAINT IF EXISTS ck_memories_source_stale_reason,
            DROP COLUMN IF EXISTS deleted_reason,
            DROP COLUMN IF EXISTS source_stale_reason,
            DROP COLUMN IF EXISTS source_stale_at
        """
    )
    op.execute(
        """
        ALTER TABLE artifact_links
            DROP COLUMN IF EXISTS connector_id,
            DROP COLUMN IF EXISTS revision_seq,
            DROP COLUMN IF EXISTS source_revision
        """
    )
