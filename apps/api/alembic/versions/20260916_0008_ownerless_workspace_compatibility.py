"""Preserve active-member access for legacy ownerless workspaces.

Revision ID: 20260916_0008
Revises: 20260909_0007
Create Date: 2026-09-16
"""

from __future__ import annotations

from alembic import op

revision = "20260916_0008"
down_revision = "20260909_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE workspaces NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "ALTER TABLE sm_workspace_bootstraps "
        "ALTER COLUMN created_by_user_id DROP NOT NULL"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION sm_sync_workspace_bootstrap()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            INSERT INTO public.sm_workspace_bootstraps (
                workspace_id, created_by_user_id, deleted_at
            ) VALUES (NEW.id, NEW.created_by_user_id, NEW.deleted_at)
            ON CONFLICT (workspace_id) DO UPDATE
            SET created_by_user_id = EXCLUDED.created_by_user_id,
                deleted_at = EXCLUDED.deleted_at;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION sm_sync_workspace_bootstrap() FROM PUBLIC")
    op.execute(
        """
        INSERT INTO sm_workspace_bootstraps (
            workspace_id, created_by_user_id, deleted_at
        )
        SELECT id, created_by_user_id, deleted_at
        FROM workspaces
        ON CONFLICT (workspace_id) DO UPDATE
        SET created_by_user_id = EXCLUDED.created_by_user_id,
            deleted_at = EXCLUDED.deleted_at
        """
    )
    op.execute("ALTER TABLE workspaces FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE workspaces NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION sm_sync_workspace_bootstrap()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF NEW.created_by_user_id IS NOT NULL THEN
                INSERT INTO public.sm_workspace_bootstraps (
                    workspace_id, created_by_user_id, deleted_at
                ) VALUES (NEW.id, NEW.created_by_user_id, NEW.deleted_at)
                ON CONFLICT (workspace_id) DO UPDATE
                SET created_by_user_id = EXCLUDED.created_by_user_id,
                    deleted_at = EXCLUDED.deleted_at;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION sm_sync_workspace_bootstrap() FROM PUBLIC")
    op.execute(
        "DELETE FROM sm_workspace_bootstraps WHERE created_by_user_id IS NULL"
    )
    op.execute(
        "ALTER TABLE sm_workspace_bootstraps "
        "ALTER COLUMN created_by_user_id SET NOT NULL"
    )
    op.execute("ALTER TABLE workspaces FORCE ROW LEVEL SECURITY")
