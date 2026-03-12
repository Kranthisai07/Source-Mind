"""Phase 3: Attribution engine, conflict resolution, and handoff schema additions.

Revision ID: 20250312_0002
Revises: 20250311_0001
Create Date: 2025-03-12
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20250312_0002"
down_revision = "20250311_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── memory_conflicts: add Phase 3 columns ────────────────────────────────
    op.add_column(
        "memory_conflicts",
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True,
                  comment="User who moved conflict to under_review status"),
    )
    op.add_column(
        "memory_conflicts",
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "memory_conflicts",
        sa.Column(
            "suggested_resolution",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Claude AI suggestion: {suggested_resolution, reasoning, merged_content, confidence}",
        ),
    )
    op.add_column(
        "memory_conflicts",
        sa.Column("revisit_at", sa.TIMESTAMP(timezone=True), nullable=True,
                  comment="For deferred conflicts: when to revisit"),
    )

    # Add FK for reviewed_by
    op.create_foreign_key(
        "fk_memory_conflicts_reviewed_by",
        "memory_conflicts", "users",
        ["reviewed_by"], ["id"],
        ondelete="SET NULL",
    )

    # ── workspace_members: add status tracking ───────────────────────────────
    op.add_column(
        "workspace_members",
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="active",
            comment="active | departing | departed",
        ),
    )
    op.add_column(
        "workspace_members",
        sa.Column("departed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # ── memories: add importance_score ──────────────────────────────────────
    op.add_column(
        "memories",
        sa.Column(
            "importance_score",
            sa.Float(),
            nullable=False,
            server_default="0.5",
            comment="Computed importance [0.0–1.0]: relation count, version, recency",
        ),
    )

    # ── handoff_records ──────────────────────────────────────────────────────
    op.create_table(
        "handoff_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("departing_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("initiated_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="in_progress",
                  comment="in_progress | completed | expired"),
        sa.Column("tier_1_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tier_2_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tier_3_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("assigned_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["departing_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["initiated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        comment="Tracks knowledge handoff when a workspace member departs",
    )
    op.create_index("ix_handoff_records_workspace", "handoff_records", ["workspace_id"])
    op.create_index("ix_handoff_records_departing", "handoff_records", ["departing_user_id"])

    # ── handoff_assignments ───────────────────────────────────────────────────
    op.create_table(
        "handoff_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("handoff_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False, comment="1, 2, or 3"),
        sa.Column("new_owner_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assigned_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["handoff_id"], ["handoff_records.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["memory_id"], ["memories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["new_owner_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        comment="Individual memory handoff assignment records",
    )
    op.create_index("ix_handoff_assignments_handoff", "handoff_assignments", ["handoff_id"])
    op.create_index("ix_handoff_assignments_memory", "handoff_assignments", ["memory_id"])


def downgrade() -> None:
    op.drop_table("handoff_assignments")
    op.drop_table("handoff_records")

    op.drop_column("memories", "importance_score")

    op.drop_column("workspace_members", "departed_at")
    op.drop_column("workspace_members", "status")

    op.drop_constraint("fk_memory_conflicts_reviewed_by", "memory_conflicts", type_="foreignkey")
    op.drop_column("memory_conflicts", "revisit_at")
    op.drop_column("memory_conflicts", "suggested_resolution")
    op.drop_column("memory_conflicts", "reviewed_at")
    op.drop_column("memory_conflicts", "reviewed_by")
