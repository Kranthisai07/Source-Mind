"""Phase A: Connector infrastructure — connector_configs, connector_sync_logs, artifact_links.

Revision ID: 20250415_0003
Revises: 20250312_0002
Create Date: 2025-04-15
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20250415_0003"
down_revision = "20250312_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── connector_configs ────────────────────────────────────────────────────
    op.create_table(
        "connector_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"),
                  nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connector_type", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(), server_default=sa.text("'{}'"),
                  nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("last_sync_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("next_sync_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "connector_type IN ('github', 'discord')",
            name="ck_connector_configs_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'error')",
            name="ck_connector_configs_status",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "connector_type", "display_name",
                            name="uq_connector_configs_ws_type_name"),
    )
    op.create_index("ix_connector_configs_workspace",
                    "connector_configs", ["workspace_id"])

    # ── connector_sync_logs ──────────────────────────────────────────────────
    op.create_table(
        "connector_sync_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"),
                  nullable=False),
        sa.Column("connector_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sync_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("artifacts_found", sa.Integer(), server_default="0",
                  nullable=False),
        sa.Column("artifacts_new", sa.Integer(), server_default="0",
                  nullable=False),
        sa.Column("artifacts_skipped", sa.Integer(), server_default="0",
                  nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "sync_type IN ('full', 'incremental')",
            name="ck_sync_logs_type",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_sync_logs_status",
        ),
        sa.ForeignKeyConstraint(["connector_id"], ["connector_configs.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_connector_sync_logs_connector",
                    "connector_sync_logs", ["connector_id"])

    # ── artifact_links ───────────────────────────────────────────────────────
    op.create_table(
        "artifact_links",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"),
                  nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_tool", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_author", sa.Text(), nullable=True),
        sa.Column("source_timestamp", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resolved_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("identity_confidence", sa.Float(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'"),
                  nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["memory_id"], ["memories.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_user_id"], ["users.id"],
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "source_tool", "source_type", "source_id",
                            name="uq_artifact_links_ws_tool_type_id"),
    )
    op.create_index("ix_artifact_links_workspace_tool",
                    "artifact_links", ["workspace_id", "source_tool"])
    op.create_index("ix_artifact_links_memory",
                    "artifact_links", ["memory_id"])
    op.create_index("ix_artifact_links_source_timestamp",
                    "artifact_links", ["source_timestamp"])


def downgrade() -> None:
    op.drop_table("artifact_links")
    op.drop_table("connector_sync_logs")
    op.drop_table("connector_configs")
