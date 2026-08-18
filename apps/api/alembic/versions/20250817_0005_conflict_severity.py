"""Conflict severity: computed tier, competing claim count, derivation block.

Revision ID: 20250817_0005
Revises: 20250816_0004
Create Date: 2026-08-17

WHAT THIS ADDS
--------------
`severity` already existed but was never computed — services/memory/relations.py
hardcoded ConflictSeverity.MEDIUM on every conflict it created, and nothing
ever updated it. This migration gives it a real vocabulary and a CHECK, and
adds the two inputs the computation needs.

  severity              default 'medium' -> 'low', CHECK IN (low, medium, critical)
  competing_claim_count NOT NULL DEFAULT 2
  blocks_derivation     NOT NULL DEFAULT FALSE

VOCABULARY CHANGE
-----------------
ConflictSeverity was LOW/MEDIUM/HIGH. 'high' is renamed to 'critical' so the
enum matches the severity ladder. The rename is data-safe here because
memory_conflicts was empty when this was written, but the UPDATE below is kept
so the migration is correct against any environment that does hold rows —
without it, the CHECK constraint would refuse to validate.

The default moves from 'medium' to 'low' because Low is the "everything else"
tier: a row that has not yet been scored should not claim to be mid-severity.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20250817_0005"
down_revision = "20250816_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows first: the CHECK is validated against current data, so any
    # legacy 'high' must be migrated before the constraint is added.
    op.execute(
        "UPDATE memory_conflicts SET severity = 'critical' WHERE severity = 'high'"
    )
    # Anything outside the vocabulary (NULL-ish or unexpected) falls to 'low'
    # rather than blocking the migration.
    op.execute(
        "UPDATE memory_conflicts SET severity = 'low' "
        "WHERE severity IS NULL OR severity NOT IN ('low', 'medium', 'critical')"
    )

    op.alter_column(
        "memory_conflicts",
        "severity",
        existing_type=sa.String(50),
        existing_nullable=False,
        server_default=sa.text("'low'"),
    )
    op.create_check_constraint(
        "ck_memory_conflicts_severity",
        "memory_conflicts",
        "severity IN ('low', 'medium', 'critical')",
    )

    op.add_column(
        "memory_conflicts",
        sa.Column(
            "competing_claim_count",
            sa.Integer(),
            nullable=False,
            server_default="2",
            comment=(
                "Distinct memories disputing the same memory, counted across "
                "all of its conflict rows. A lone pairwise conflict is 2."
            ),
        ),
    )
    op.add_column(
        "memory_conflicts",
        sa.Column(
            "blocks_derivation",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="True only while severity='critical' and unresolved",
        ),
    )

    # Severity is filtered on in the conflict list endpoint alongside status.
    op.create_index(
        "ix_memory_conflicts_severity_status",
        "memory_conflicts",
        ["severity", "status"],
    )


def downgrade() -> None:
    """Reverse the changes.

    OPERATOR WARNING — 'critical' has no representation after this runs. The
    pre-0005 vocabulary was low/medium/high, so any row scored 'critical' is
    mapped back to 'high' below. That is lossless in both directions only
    because the two names denote the same tier; if you have introduced a
    genuine 'high' tier distinct from 'critical', reconcile it before
    downgrading or the distinction is silently collapsed.

    OPERATOR WARNING — competing_claim_count and blocks_derivation are dropped
    outright. Both are derived values that compute_conflict_severity() can
    regenerate, so no backup is required, but any external consumer reading
    blocks_derivation loses its signal immediately and will see no conflict as
    blocking.
    """
    op.drop_index("ix_memory_conflicts_severity_status", table_name="memory_conflicts")
    op.drop_column("memory_conflicts", "blocks_derivation")
    op.drop_column("memory_conflicts", "competing_claim_count")

    op.drop_constraint(
        "ck_memory_conflicts_severity", "memory_conflicts", type_="check"
    )
    # Map back to the pre-0005 vocabulary before restoring the old default,
    # so no row is left holding a value the old code cannot interpret.
    op.execute(
        "UPDATE memory_conflicts SET severity = 'high' WHERE severity = 'critical'"
    )
    op.alter_column(
        "memory_conflicts",
        "severity",
        existing_type=sa.String(50),
        existing_nullable=False,
        server_default=sa.text("'medium'"),
    )
