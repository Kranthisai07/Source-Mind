"""Serialize active-owner removal checks per workspace.

Revision ID: 20260916_0009
Revises: 20260916_0008
Create Date: 2026-09-16
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from alembic import context, op

revision = "20260916_0009"
down_revision = "20260916_0008"
branch_labels = None
depends_on = None

_LEGACY_CONNECTOR_TARGETS = {
    None,
    "0001_initial",
    "20250312_0002",
    "20250415_0003",
    "20250816_0004",
    "20250817_0005",
}


def _assert_0005_rollback_compatibility() -> None:
    bind = op.get_bind()
    try:
        bind.execute(
            text(
                "ALTER TABLE connector_configs "
                "ADD CONSTRAINT ck_connector_configs_0005_preflight "
                "CHECK (connector_type IN ('github', 'discord'))"
            )
        )
    except IntegrityError as exc:
        raise RuntimeError(
            "Cannot begin rollback to 20250817_0005 while connector_configs contains "
            "connector types unsupported by 0005. Preserve the upgraded "
            "database; do not delete or relabel connector data."
        ) from exc
    bind.execute(
        text(
            "ALTER TABLE connector_configs "
            "DROP CONSTRAINT ck_connector_configs_0005_preflight"
        )
    )


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
    if context.get_revision_argument() in _LEGACY_CONNECTOR_TARGETS:
        _assert_0005_rollback_compatibility()

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
