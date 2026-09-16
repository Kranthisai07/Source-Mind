"""Serialize active-owner removal checks per workspace.

Revision ID: 20260916_0009
Revises: 20260916_0008
Create Date: 2026-09-16
"""

from __future__ import annotations

from alembic import op

revision = "20260916_0009"
down_revision = "20260916_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION sm_protect_last_workspace_owner()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF OLD.role = 'owner'
               AND OLD.status = 'active'
               AND OLD.departed_at IS NULL THEN
                IF TG_OP = 'DELETE' THEN
                    PERFORM 1
                    FROM public.workspaces
                    WHERE id = OLD.workspace_id
                    FOR UPDATE;

                    IF NOT EXISTS (
                        SELECT 1
                        FROM public.sm_workspace_access_grants AS other_owner
                        WHERE other_owner.workspace_id = OLD.workspace_id
                          AND other_owner.user_id <> OLD.user_id
                          AND other_owner.role = 'owner'
                          AND other_owner.status = 'active'
                          AND other_owner.departed_at IS NULL
                    ) THEN
                        RAISE EXCEPTION 'last active workspace owner cannot be removed'
                            USING ERRCODE = '42501';
                    END IF;
                    RETURN OLD;
                END IF;

                IF (
                    NEW.role <> 'owner'
                    OR NEW.status <> 'active'
                    OR NEW.departed_at IS NOT NULL
                ) THEN
                    PERFORM 1
                    FROM public.workspaces
                    WHERE id = OLD.workspace_id
                    FOR UPDATE;

                    IF NOT EXISTS (
                        SELECT 1
                        FROM public.sm_workspace_access_grants AS other_owner
                        WHERE other_owner.workspace_id = OLD.workspace_id
                          AND other_owner.user_id <> OLD.user_id
                          AND other_owner.role = 'owner'
                          AND other_owner.status = 'active'
                          AND other_owner.departed_at IS NULL
                    ) THEN
                        RAISE EXCEPTION 'last active workspace owner cannot be removed'
                            USING ERRCODE = '42501';
                    END IF;
                END IF;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION sm_protect_last_workspace_owner() FROM PUBLIC")


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION sm_protect_last_workspace_owner()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF OLD.role = 'owner'
               AND OLD.status = 'active'
               AND OLD.departed_at IS NULL THEN
                IF TG_OP = 'DELETE' THEN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM public.sm_workspace_access_grants AS other_owner
                        WHERE other_owner.workspace_id = OLD.workspace_id
                          AND other_owner.user_id <> OLD.user_id
                          AND other_owner.role = 'owner'
                          AND other_owner.status = 'active'
                          AND other_owner.departed_at IS NULL
                    ) THEN
                        RAISE EXCEPTION 'last active workspace owner cannot be removed'
                            USING ERRCODE = '42501';
                    END IF;
                    RETURN OLD;
                END IF;

                IF (
                    NEW.role <> 'owner'
                    OR NEW.status <> 'active'
                    OR NEW.departed_at IS NOT NULL
                ) AND NOT EXISTS (
                    SELECT 1
                    FROM public.sm_workspace_access_grants AS other_owner
                    WHERE other_owner.workspace_id = OLD.workspace_id
                      AND other_owner.user_id <> OLD.user_id
                      AND other_owner.role = 'owner'
                      AND other_owner.status = 'active'
                      AND other_owner.departed_at IS NULL
                ) THEN
                    RAISE EXCEPTION 'last active workspace owner cannot be removed'
                        USING ERRCODE = '42501';
                END IF;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION sm_protect_last_workspace_owner() FROM PUBLIC")
