"""Append-only workspace audit events: who changed membership, roles, invitations, exports.

Revision ID: 20261031_0018
Revises: 20261031_0017
Create Date: 2026-10-31
"""
# ruff: noqa: S608

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any

from sqlalchemy import text

from alembic import context, op

revision = "20261031_0018"
down_revision = "20261031_0017"
branch_labels = None
depends_on = None

RUNTIME_ROLE_ENV = "SOURCEMIND_RUNTIME_ROLE"
NEW_TABLES = ("workspace_audit_events",)

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
    table = "workspace_audit_events"
    op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
    if _table_owner(table) != role:
        op.execute(f'REVOKE ALL ON TABLE {table} FROM "{role}"')
        op.execute(f'GRANT SELECT, INSERT ON TABLE {table} TO "{role}"')
    op.execute(f"REVOKE ALL ON SEQUENCE {table}_id_seq FROM PUBLIC")
    op.execute(f'REVOKE ALL ON SEQUENCE {table}_id_seq FROM "{role}"')
    op.execute(f'GRANT USAGE, SELECT ON SEQUENCE {table}_id_seq TO "{role}"')


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
                WHERE c.relname IN ({tables}, 'workspace_audit_events_id_seq')
                  AND a.grantee <> 0 AND a.grantee <> c.relowner
                  AND pg_get_userbyid(a.grantee) <> '{role}'
            LOOP
                EXECUTE format('REVOKE ALL ON %s public.%I FROM %I',
                    CASE WHEN r.relname = 'workspace_audit_events_id_seq'
                         THEN 'SEQUENCE' ELSE 'TABLE' END,
                    r.relname, r.grantee_name);
            END LOOP;
            FOR r IN
                SELECT p.oid::regprocedure::text AS proc,
                       pg_get_userbyid(a.grantee) AS grantee_name
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace AND n.nspname = 'public'
                CROSS JOIN LATERAL aclexplode(p.proacl) a
                WHERE p.proname = 'prevent_workspace_audit_mutation'
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

    # Deliberately no foreign keys, as in mcp_key_events: a cascade or SET NULL would have to
    # update or delete these rows, and they are append-only. The record must outlive a deleted
    # user or workspace row. It holds ids, roles and an invitee address, never content or tokens.
    op.execute(
        """
        CREATE TABLE workspace_audit_events (
            id bigserial PRIMARY KEY,
            workspace_id uuid NOT NULL,
            actor_user_id uuid NOT NULL,
            event text NOT NULL,
            target_user_id uuid,
            target_email text,
            role_before text,
            role_after text,
            at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_workspace_audit_events_event CHECK (event IN (
                'member_added', 'member_removed', 'role_changed',
                'invitation_created', 'invitation_revoked', 'invitation_accepted',
                'workspace_exported'
            )),
            CONSTRAINT ck_workspace_audit_events_roles CHECK (
                (role_before IS NULL OR role_before IN ('owner', 'admin', 'member', 'viewer'))
                AND (role_after IS NULL OR role_after IN ('owner', 'admin', 'member', 'viewer'))
            ),
            CONSTRAINT ck_workspace_audit_events_email CHECK (
                target_email IS NULL
                OR (target_email = lower(target_email) AND length(target_email) <= 254)
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_workspace_audit_events_workspace_id "
        "ON workspace_audit_events (workspace_id, id DESC)"
    )
    op.execute(
        """
        CREATE FUNCTION prevent_workspace_audit_mutation() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            RAISE EXCEPTION 'workspace_audit_events rows are append-only';
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION prevent_workspace_audit_mutation() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER workspace_audit_events_append_only "
        "BEFORE UPDATE OR DELETE ON workspace_audit_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_workspace_audit_mutation()"
    )

    op.execute("ALTER TABLE workspace_audit_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspace_audit_events FORCE ROW LEVEL SECURITY")

    ws = "workspace_audit_events.workspace_id"
    active = _active_access(ws)
    admin = _admin_access(ws)
    # Everything but an acceptance is written by a current founder or admin. An acceptance is
    # written by the person who just joined, who may hold any role, so it is allowed only for the
    # accepting user, in a workspace where they hold an invitation they accepted themselves.
    op.execute(
        "CREATE POLICY workspace_audit_events_insert ON workspace_audit_events "
        "FOR INSERT WITH CHECK ("
        f"workspace_audit_events.actor_user_id = {_USER_ID} AND ("
        f"(workspace_audit_events.event <> 'invitation_accepted' AND ({admin})) "
        "OR (workspace_audit_events.event = 'invitation_accepted' "
        f"AND ({active}) AND EXISTS ("
        "SELECT 1 FROM workspace_invitations AS audit_invitation "
        f"WHERE audit_invitation.workspace_id = {ws} "
        f"AND audit_invitation.accepted_by = {_USER_ID} "
        "AND audit_invitation.accepted_at IS NOT NULL))))"
    )
    op.execute(
        "CREATE POLICY workspace_audit_events_select ON workspace_audit_events "
        f"FOR SELECT USING ({admin})"
    )

    _grant_runtime_privileges(role)
    sweep_other_grantees(op.execute, role)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS workspace_audit_events_append_only ON workspace_audit_events"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_workspace_audit_mutation()")
    op.execute("DROP TABLE IF EXISTS workspace_audit_events")
