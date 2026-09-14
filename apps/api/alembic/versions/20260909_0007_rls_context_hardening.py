"""Prevent workspace-context substitution from granting tenant membership.

Revision ID: 20260909_0007
Revises: 20260908_0006
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260909_0007"
down_revision = "20260908_0006"
branch_labels = None
depends_on = None

_USER_ID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_WORKSPACE_ID = "NULLIF(current_setting('app.current_workspace_id', true), '')::uuid"
_ALL_ROLES = "ARRAY['viewer', 'member', 'admin', 'owner']::text[]"
_ADMIN_ROLES = "ARRAY['admin', 'owner']::text[]"
_OWNER_ROLE = "ARRAY['owner']::text[]"


def upgrade() -> None:
    op.execute("ALTER TABLE workspaces NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspace_members NO FORCE ROW LEVEL SECURITY")

    op.add_column(
        "workspaces",
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_workspaces_created_by_user",
        "workspaces",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_workspaces_created_by_user_id",
        "workspaces",
        ["created_by_user_id"],
    )
    op.execute(
        """
        UPDATE workspaces
        SET created_by_user_id = (
            SELECT member.user_id
            FROM workspace_members AS member
            WHERE member.workspace_id = workspaces.id
              AND member.role = 'owner'
              AND member.status = 'active'
              AND member.departed_at IS NULL
            ORDER BY member.created_at, member.id
            LIMIT 1
        )
        WHERE created_by_user_id IS NULL
        """
    )

    op.execute(
        """
        CREATE TABLE sm_workspace_bootstraps (
            workspace_id uuid PRIMARY KEY
                REFERENCES workspaces(id) ON DELETE CASCADE,
            created_by_user_id uuid NOT NULL,
            deleted_at timestamptz
        )
        """
    )
    op.execute(
        """
        CREATE TABLE sm_workspace_access_grants (
            workspace_id uuid NOT NULL
                REFERENCES workspaces(id) ON DELETE CASCADE,
            user_id uuid NOT NULL
                REFERENCES users(id) ON DELETE CASCADE,
            role text NOT NULL,
            status text NOT NULL,
            departed_at timestamptz,
            PRIMARY KEY (workspace_id, user_id),
            CONSTRAINT ck_sm_workspace_access_role
                CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
            CONSTRAINT ck_sm_workspace_access_status
                CHECK (status IN ('active', 'departing', 'departed'))
        )
        """
    )
    op.execute(
        """
        INSERT INTO sm_workspace_bootstraps (
            workspace_id, created_by_user_id, deleted_at
        )
        SELECT id, created_by_user_id, deleted_at
        FROM workspaces
        WHERE created_by_user_id IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT INTO sm_workspace_access_grants (
            workspace_id, user_id, role, status, departed_at
        )
        SELECT workspace_id, user_id, role, status, departed_at
        FROM workspace_members
        """
    )
    op.execute("REVOKE ALL ON sm_workspace_bootstraps FROM PUBLIC")
    op.execute("REVOKE ALL ON sm_workspace_access_grants FROM PUBLIC")
    op.execute(
        """
        DO $$
        DECLARE
            grantee_name text;
        BEGIN
            FOR grantee_name IN
                SELECT DISTINCT grantee
                FROM information_schema.role_table_grants
                WHERE table_schema = 'public'
                  AND table_name IN (
                      'sm_workspace_bootstraps',
                      'sm_workspace_access_grants'
                  )
                  AND grantee NOT IN (current_user, 'PUBLIC')
            LOOP
                EXECUTE format(
                    'REVOKE ALL PRIVILEGES ON TABLE '
                    'public.sm_workspace_bootstraps, '
                    'public.sm_workspace_access_grants FROM %I',
                    grantee_name
                );
            END LOOP;
        END
        $$
        """
    )

    op.execute(
        """
        CREATE FUNCTION sm_has_active_workspace_access(
            target_workspace_id uuid,
            allowed_roles text[]
        ) RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT EXISTS (
                SELECT 1
                FROM public.sm_workspace_access_grants AS access_grant
                JOIN public.sm_workspace_bootstraps AS bootstrap
                  ON bootstrap.workspace_id = access_grant.workspace_id
                WHERE access_grant.workspace_id = target_workspace_id
                  AND access_grant.user_id =
                      NULLIF(current_setting('app.current_user_id', true), '')::uuid
                  AND access_grant.role = ANY(allowed_roles)
                  AND access_grant.status = 'active'
                  AND access_grant.departed_at IS NULL
                  AND bootstrap.deleted_at IS NULL
            )
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION sm_can_bootstrap_workspace_owner(
            target_workspace_id uuid,
            target_user_id uuid,
            target_role text,
            target_status text,
            target_departed_at timestamptz
        ) RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT target_user_id =
                       NULLIF(current_setting('app.current_user_id', true), '')::uuid
               AND target_role = 'owner'
               AND target_status = 'active'
               AND target_departed_at IS NULL
               AND EXISTS (
                    SELECT 1
                    FROM public.sm_workspace_bootstraps AS bootstrap
                    WHERE bootstrap.workspace_id = target_workspace_id
                      AND bootstrap.created_by_user_id = target_user_id
                      AND bootstrap.deleted_at IS NULL
               )
               AND NOT EXISTS (
                    SELECT 1
                    FROM public.sm_workspace_access_grants AS existing_grant
                    WHERE existing_grant.workspace_id = target_workspace_id
               )
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION sm_is_workspace_creator_without_owner(
            target_workspace_id uuid,
            target_creator_id uuid
        ) RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT target_creator_id =
                       NULLIF(current_setting('app.current_user_id', true), '')::uuid
               AND NOT EXISTS (
                    SELECT 1
                    FROM public.sm_workspace_access_grants AS existing_grant
                    WHERE existing_grant.workspace_id = target_workspace_id
               )
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION sm_sync_workspace_bootstrap()
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
    op.execute(
        """
        CREATE FUNCTION sm_preserve_workspace_creator()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id THEN
                IF NEW.created_by_user_id IS NULL THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION 'workspace creator is immutable'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION sm_preserve_workspace_member_identity()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
               OR NEW.user_id IS DISTINCT FROM OLD.user_id THEN
                RAISE EXCEPTION 'workspace membership identity is immutable'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION sm_sync_workspace_access_grant()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                DELETE FROM public.sm_workspace_access_grants
                WHERE workspace_id = OLD.workspace_id
                  AND user_id = OLD.user_id;
                RETURN OLD;
            END IF;

            INSERT INTO public.sm_workspace_access_grants (
                workspace_id, user_id, role, status, departed_at
            ) VALUES (
                NEW.workspace_id, NEW.user_id, NEW.role, NEW.status,
                NEW.departed_at
            )
            ON CONFLICT (workspace_id, user_id) DO UPDATE
            SET role = EXCLUDED.role,
                status = EXCLUDED.status,
                departed_at = EXCLUDED.departed_at;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION sm_protect_last_workspace_owner()
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
    for function_name in (
        "sm_sync_workspace_bootstrap()",
        "sm_preserve_workspace_creator()",
        "sm_preserve_workspace_member_identity()",
        "sm_sync_workspace_access_grant()",
        "sm_protect_last_workspace_owner()",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {function_name} FROM PUBLIC")

    op.execute(
        """
        CREATE TRIGGER sm_workspace_bootstrap_sync
        AFTER INSERT OR UPDATE OF created_by_user_id, deleted_at ON workspaces
        FOR EACH ROW EXECUTE FUNCTION sm_sync_workspace_bootstrap()
        """
    )
    op.execute(
        """
        CREATE TRIGGER sm_workspace_creator_immutable
        BEFORE UPDATE OF created_by_user_id ON workspaces
        FOR EACH ROW EXECUTE FUNCTION sm_preserve_workspace_creator()
        """
    )
    op.execute(
        """
        CREATE TRIGGER sm_workspace_member_identity_immutable
        BEFORE UPDATE OF workspace_id, user_id ON workspace_members
        FOR EACH ROW EXECUTE FUNCTION sm_preserve_workspace_member_identity()
        """
    )
    op.execute(
        """
        CREATE TRIGGER sm_workspace_owner_guard
        BEFORE UPDATE OR DELETE ON workspace_members
        FOR EACH ROW EXECUTE FUNCTION sm_protect_last_workspace_owner()
        """
    )
    op.execute(
        """
        CREATE TRIGGER sm_workspace_access_grant_sync
        AFTER INSERT OR UPDATE OR DELETE ON workspace_members
        FOR EACH ROW EXECUTE FUNCTION sm_sync_workspace_access_grant()
        """
    )

    op.execute("DROP POLICY IF EXISTS sm_workspace_isolation ON workspaces")
    for policy_name in (
        "sm_workspace_select",
        "sm_workspace_insert",
        "sm_workspace_update",
        "sm_workspace_delete",
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON workspaces")
    op.execute(
        f"""
        CREATE POLICY sm_workspace_select ON workspaces
        FOR SELECT USING (
            ({_WORKSPACE_ID} IS NULL OR id = {_WORKSPACE_ID})
            AND deleted_at IS NULL
            AND (
                sm_has_active_workspace_access(id, {_ALL_ROLES})
                OR sm_is_workspace_creator_without_owner(id, created_by_user_id)
            )
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY sm_workspace_insert ON workspaces
        FOR INSERT WITH CHECK (
            ({_WORKSPACE_ID} IS NULL OR id = {_WORKSPACE_ID})
            AND created_by_user_id = {_USER_ID}
            AND deleted_at IS NULL
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY sm_workspace_update ON workspaces
        FOR UPDATE USING (
            ({_WORKSPACE_ID} IS NULL OR id = {_WORKSPACE_ID})
            AND sm_has_active_workspace_access(id, {_ADMIN_ROLES})
        ) WITH CHECK (
            ({_WORKSPACE_ID} IS NULL OR id = {_WORKSPACE_ID})
            AND sm_has_active_workspace_access(id, {_ADMIN_ROLES})
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY sm_workspace_delete ON workspaces
        FOR DELETE USING (
            ({_WORKSPACE_ID} IS NULL OR id = {_WORKSPACE_ID})
            AND sm_has_active_workspace_access(id, {_OWNER_ROLE})
        )
        """
    )

    op.execute(
        "DROP POLICY IF EXISTS sm_workspace_members_isolation ON workspace_members"
    )
    for policy_name in (
        "sm_workspace_members_select",
        "sm_workspace_members_insert",
        "sm_workspace_members_update",
        "sm_workspace_members_delete",
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON workspace_members")
    op.execute(
        f"""
        CREATE POLICY sm_workspace_members_select ON workspace_members
        FOR SELECT USING (
            ({_WORKSPACE_ID} IS NULL OR workspace_id = {_WORKSPACE_ID})
            AND (
                sm_has_active_workspace_access(workspace_id, {_ALL_ROLES})
                OR sm_can_bootstrap_workspace_owner(
                    workspace_id, user_id, role, status, departed_at
                )
            )
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY sm_workspace_members_insert ON workspace_members
        FOR INSERT WITH CHECK (
            ({_WORKSPACE_ID} IS NULL OR workspace_id = {_WORKSPACE_ID})
            AND (
                sm_can_bootstrap_workspace_owner(
                    workspace_id, user_id, role, status, departed_at
                )
                OR (
                    sm_has_active_workspace_access(workspace_id, {_ADMIN_ROLES})
                    AND (
                        role <> 'owner'
                        OR sm_has_active_workspace_access(
                            workspace_id, {_OWNER_ROLE}
                        )
                    )
                )
            )
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY sm_workspace_members_update ON workspace_members
        FOR UPDATE USING (
            ({_WORKSPACE_ID} IS NULL OR workspace_id = {_WORKSPACE_ID})
            AND sm_has_active_workspace_access(workspace_id, {_ADMIN_ROLES})
            AND (
                role <> 'owner'
                OR sm_has_active_workspace_access(workspace_id, {_OWNER_ROLE})
            )
        ) WITH CHECK (
            ({_WORKSPACE_ID} IS NULL OR workspace_id = {_WORKSPACE_ID})
            AND sm_has_active_workspace_access(workspace_id, {_ADMIN_ROLES})
            AND (
                role <> 'owner'
                OR sm_has_active_workspace_access(workspace_id, {_OWNER_ROLE})
            )
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY sm_workspace_members_delete ON workspace_members
        FOR DELETE USING (
            ({_WORKSPACE_ID} IS NULL OR workspace_id = {_WORKSPACE_ID})
            AND sm_has_active_workspace_access(workspace_id, {_ADMIN_ROLES})
            AND (
                role <> 'owner'
                OR sm_has_active_workspace_access(workspace_id, {_OWNER_ROLE})
            )
        )
        """
    )
    op.execute("ALTER TABLE workspaces FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspace_members FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for policy_name in (
        "sm_workspace_members_select",
        "sm_workspace_members_insert",
        "sm_workspace_members_update",
        "sm_workspace_members_delete",
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON workspace_members")
    for policy_name in (
        "sm_workspace_select",
        "sm_workspace_insert",
        "sm_workspace_update",
        "sm_workspace_delete",
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON workspaces")

    op.execute(
        f"""
        CREATE POLICY sm_workspace_members_isolation ON workspace_members
        USING (
            (user_id = {_USER_ID} AND status = 'active' AND departed_at IS NULL)
            OR workspace_id = {_WORKSPACE_ID}
        )
        WITH CHECK (user_id = {_USER_ID} OR workspace_id = {_WORKSPACE_ID})
        """
    )
    op.execute(
        """
        CREATE POLICY sm_workspace_isolation ON workspaces
        USING (
            deleted_at IS NULL
            AND (
                id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
                OR EXISTS (
                    SELECT 1 FROM workspace_members AS own_membership
                    WHERE own_membership.workspace_id = workspaces.id
                      AND own_membership.user_id =
                          NULLIF(current_setting('app.current_user_id', true), '')::uuid
                      AND own_membership.status = 'active'
                      AND own_membership.departed_at IS NULL
                )
            )
        )
        WITH CHECK (NULLIF(current_setting('app.current_user_id', true), '')::uuid IS NOT NULL)
        """
    )

    op.execute(
        "DROP TRIGGER IF EXISTS sm_workspace_access_grant_sync ON workspace_members"
    )
    op.execute("DROP TRIGGER IF EXISTS sm_workspace_owner_guard ON workspace_members")
    op.execute(
        "DROP TRIGGER IF EXISTS sm_workspace_member_identity_immutable "
        "ON workspace_members"
    )
    op.execute("DROP TRIGGER IF EXISTS sm_workspace_creator_immutable ON workspaces")
    op.execute("DROP TRIGGER IF EXISTS sm_workspace_bootstrap_sync ON workspaces")
    op.execute("DROP FUNCTION IF EXISTS sm_protect_last_workspace_owner()")
    op.execute("DROP FUNCTION IF EXISTS sm_sync_workspace_access_grant()")
    op.execute("DROP FUNCTION IF EXISTS sm_preserve_workspace_member_identity()")
    op.execute("DROP FUNCTION IF EXISTS sm_preserve_workspace_creator()")
    op.execute("DROP FUNCTION IF EXISTS sm_sync_workspace_bootstrap()")
    op.execute(
        "DROP FUNCTION sm_can_bootstrap_workspace_owner(uuid, uuid, text, text, timestamptz)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS sm_is_workspace_creator_without_owner(uuid, uuid)"
    )
    op.execute("DROP FUNCTION IF EXISTS sm_has_active_workspace_access(uuid, text[])")
    op.execute("DROP TABLE IF EXISTS sm_workspace_access_grants")
    op.execute("DROP TABLE IF EXISTS sm_workspace_bootstraps")
    op.drop_index("ix_workspaces_created_by_user_id", table_name="workspaces")
    op.drop_constraint(
        "fk_workspaces_created_by_user", "workspaces", type_="foreignkey"
    )
    op.drop_column("workspaces", "created_by_user_id")
