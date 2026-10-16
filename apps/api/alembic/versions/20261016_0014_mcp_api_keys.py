"""Workspace-scoped MCP API keys and append-only key audit events.

Revision ID: 20261016_0014
Revises: 20261015_0013
Create Date: 2026-10-16
"""
# ruff: noqa: S608

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any

from sqlalchemy import text

from alembic import context, op

revision = "20261016_0014"
down_revision = "20261015_0013"
branch_labels = None
depends_on = None

RUNTIME_ROLE_ENV = "SOURCEMIND_RUNTIME_ROLE"
NEW_TABLES = ("mcp_api_keys", "mcp_key_events")

_ROLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_USER_ID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_WORKSPACE_ID = "NULLIF(current_setting('app.current_workspace_id', true), '')::uuid"


def _active_access(workspace_expression: str) -> str:
    return f"""
        ({_WORKSPACE_ID} IS NULL OR {workspace_expression} = {_WORKSPACE_ID})
        AND EXISTS (
            SELECT 1
            FROM workspace_members AS access_membership
            JOIN workspaces AS access_workspace
              ON access_workspace.id = access_membership.workspace_id
            WHERE access_membership.workspace_id = {workspace_expression}
              AND access_membership.user_id = {_USER_ID}
              AND access_membership.status = 'active'
              AND access_membership.departed_at IS NULL
              AND access_workspace.deleted_at IS NULL
        )
    """


def _admin_access(workspace_expression: str) -> str:
    return _active_access(workspace_expression).rstrip().rstrip(")") + """
              AND access_membership.role IN ('owner', 'admin')
        )
    """


def required_runtime_role() -> str:
    role = (os.environ.get(RUNTIME_ROLE_ENV) or "").strip()
    if not role:
        raise RuntimeError(
            f"{RUNTIME_ROLE_ENV} must name the restricted runtime database role."
        )
    if not _ROLE_NAME.fullmatch(role):
        raise RuntimeError(f"{RUNTIME_ROLE_ENV} must be a plain PostgreSQL role identifier.")
    return role


def _assert_role_exists(role: str) -> None:
    if context.is_offline_mode():
        return
    exists = (
        op.get_bind()
        .execute(text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role})
        .scalar()
    )
    if not exists:
        raise RuntimeError(f"{RUNTIME_ROLE_ENV} names missing role {role!r}.")


def _table_owner(table: str) -> str | None:
    if context.is_offline_mode():
        return None
    owner = (
        op.get_bind()
        .execute(
            text(
                "SELECT tableowner FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename = :table"
            ),
            {"table": table},
        )
        .scalar()
    )
    return str(owner) if owner is not None else None


def _grant_runtime_privileges(role: str) -> None:
    for table in NEW_TABLES:
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
        if _table_owner(table) != role:
            op.execute(f'REVOKE ALL ON TABLE {table} FROM "{role}"')

    if _table_owner("mcp_api_keys") != role:
        op.execute(f'GRANT SELECT, INSERT ON TABLE mcp_api_keys TO "{role}"')
        op.execute(
            f'GRANT UPDATE (revoked_at, revoked_by, last_used_at) '
            f'ON TABLE mcp_api_keys TO "{role}"'
        )
    if _table_owner("mcp_key_events") != role:
        op.execute(f'GRANT SELECT, INSERT ON TABLE mcp_key_events TO "{role}"')

    op.execute("REVOKE ALL ON SEQUENCE mcp_key_events_id_seq FROM PUBLIC")
    op.execute(f'REVOKE ALL ON SEQUENCE mcp_key_events_id_seq FROM "{role}"')
    op.execute(f'GRANT USAGE, SELECT ON SEQUENCE mcp_key_events_id_seq TO "{role}"')


def sweep_other_grantees(execute: Callable[[str], Any], role: str) -> None:
    tables = ", ".join(f"'{table}'" for table in NEW_TABLES)
    execute(
        f"""
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN
                SELECT c.relname, pg_get_userbyid(a.grantee) AS grantee_name
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
                CROSS JOIN LATERAL aclexplode(c.relacl) a
                WHERE c.relname IN ({tables}, 'mcp_key_events_id_seq')
                  AND a.grantee <> 0 AND a.grantee <> c.relowner
                  AND pg_get_userbyid(a.grantee) <> '{role}'
            LOOP
                EXECUTE format('REVOKE ALL ON %s public.%I FROM %I',
                    CASE WHEN r.relname = 'mcp_key_events_id_seq' THEN 'SEQUENCE' ELSE 'TABLE' END,
                    r.relname, r.grantee_name);
            END LOOP;
            FOR r IN
                SELECT p.oid::regprocedure::text AS proc,
                       pg_get_userbyid(a.grantee) AS grantee_name
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace AND n.nspname = 'public'
                CROSS JOIN LATERAL aclexplode(p.proacl) a
                WHERE p.proname = 'prevent_mcp_key_event_mutation'
                  AND a.grantee <> 0 AND a.grantee <> p.proowner
            LOOP
                EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I', r.proc, r.grantee_name);
            END LOOP;
        END
        $$
        """
    )


def upgrade() -> None:
    role = required_runtime_role()
    _assert_role_exists(role)

    op.execute(
        """
        CREATE TABLE mcp_api_keys (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name varchar(100) NOT NULL,
            key_prefix varchar(12) NOT NULL,
            key_hash char(64) NOT NULL UNIQUE,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            revoked_at timestamptz,
            revoked_by uuid REFERENCES users(id) ON DELETE SET NULL,
            last_used_at timestamptz,
            CONSTRAINT ck_mcp_api_keys_expiry CHECK (expires_at > created_at),
            CONSTRAINT ck_mcp_api_keys_hash CHECK (key_hash ~ '^[0-9a-f]{64}$')
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_mcp_api_keys_workspace_user "
        "ON mcp_api_keys (workspace_id, user_id)"
    )
    op.execute(
        """
        CREATE TABLE mcp_key_events (
            id bigserial PRIMARY KEY,
            key_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            actor_user_id uuid NOT NULL,
            event text NOT NULL,
            at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_mcp_key_events_event CHECK (event IN ('created', 'revoked'))
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_mcp_key_event_mutation() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            RAISE EXCEPTION 'mcp_key_events rows are append-only';
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION prevent_mcp_key_event_mutation() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER mcp_key_events_append_only "
        "BEFORE UPDATE OR DELETE ON mcp_key_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_mcp_key_event_mutation()"
    )

    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    active = _active_access("mcp_api_keys.workspace_id")
    admin = _admin_access("mcp_api_keys.workspace_id")
    own_or_admin = f"((mcp_api_keys.user_id = {_USER_ID} AND ({active})) OR ({admin}))"
    op.execute(
        f"CREATE POLICY mcp_api_keys_select ON mcp_api_keys FOR SELECT USING ({own_or_admin})"
    )
    op.execute(
        "CREATE POLICY mcp_api_keys_insert ON mcp_api_keys FOR INSERT WITH CHECK "
        f"(mcp_api_keys.user_id = {_USER_ID} AND ({active}))"
    )
    op.execute(
        "CREATE POLICY mcp_api_keys_update ON mcp_api_keys FOR UPDATE "
        f"USING ({own_or_admin}) "
        f"WITH CHECK (({own_or_admin}) AND mcp_api_keys.revoked_at IS NOT NULL)"
    )

    # Lookup is deliberately an ordinary forced-RLS policy, not a SECURITY DEFINER function.
    # Possession of one full SHA-256 hash reveals at most that one live row; an unset/empty GUC
    # never widens visibility. Column grants keep this path from changing key identity or expiry.
    lookup = """
        key_hash = NULLIF(current_setting('app.mcp_key_hash', true), '')
        AND revoked_at IS NULL
        AND expires_at > now()
    """
    op.execute(
        f"CREATE POLICY mcp_key_lookup ON mcp_api_keys FOR SELECT USING ({lookup})"
    )
    op.execute(
        f"CREATE POLICY mcp_key_touch ON mcp_api_keys FOR UPDATE "
        f"USING ({lookup}) WITH CHECK ({lookup})"
    )

    event_active = _active_access("mcp_key_events.workspace_id")
    event_admin = _admin_access("mcp_key_events.workspace_id")
    op.execute(
        "CREATE POLICY mcp_key_events_insert ON mcp_key_events FOR INSERT WITH CHECK "
        f"(mcp_key_events.actor_user_id = {_USER_ID} AND ({event_active}))"
    )
    op.execute(
        "CREATE POLICY mcp_key_events_select ON mcp_key_events FOR SELECT "
        f"USING ({event_admin})"
    )

    _grant_runtime_privileges(role)
    sweep_other_grantees(op.execute, role)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS mcp_key_events_append_only ON mcp_key_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_mcp_key_event_mutation()")
    op.execute("DROP TABLE IF EXISTS mcp_key_events")
    op.execute("DROP TABLE IF EXISTS mcp_api_keys")
