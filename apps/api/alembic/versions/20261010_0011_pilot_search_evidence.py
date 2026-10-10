"""Append-only search and rating evidence for the manual pilot.

Revision ID: 20261010_0011
Revises: 20261005_0010
Create Date: 2026-10-10

Both tables are workspace-scoped, FORCE-RLS protected, and append-only. The
restricted runtime role is explicit and receives only SELECT and INSERT. The
downgrade refuses to remove either table once evidence exists.
"""
# ruff: noqa: S608

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any

from sqlalchemy import text

from alembic import context, op

revision = "20261010_0011"
down_revision = "20261005_0010"
branch_labels = None
depends_on = None

EVENTS_TABLE = "search_events"
RATINGS_TABLE = "search_ratings"
RUNTIME_ROLE_ENV = "SOURCEMIND_RUNTIME_ROLE"
RUNTIME_PRIVILEGES = "SELECT, INSERT"

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
              AND access_membership.role IN ('owner', 'admin')
        )
    """


def required_runtime_role() -> str:
    role = (os.environ.get(RUNTIME_ROLE_ENV) or "").strip()
    if not role:
        raise RuntimeError(
            f"{RUNTIME_ROLE_ENV} must name the restricted runtime database role "
            "before creating pilot evidence tables."
        )
    if not _ROLE_NAME.fullmatch(role):
        raise RuntimeError(
            f"{RUNTIME_ROLE_ENV} must be a plain PostgreSQL role identifier."
        )
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


def grant_runtime_privileges(
    execute: Callable[[str], Any],
    role: str,
    table: str,
    table_owner: str | None = None,
) -> None:
    execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
    if table_owner is not None and role == table_owner:
        return
    execute(f'REVOKE ALL ON TABLE {table} FROM "{role}"')
    execute(f'GRANT {RUNTIME_PRIVILEGES} ON TABLE {table} TO "{role}"')


def upgrade() -> None:
    role = required_runtime_role()
    _assert_role_exists(role)

    op.execute(
        f"""
        CREATE TABLE {EVENTS_TABLE} (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
            requester_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            query text NOT NULL,
            search_parameters jsonb NOT NULL,
            request_id text NULL,
            result_snapshot jsonb NOT NULL,
            memory_ids jsonb NOT NULL,
            document_ids jsonb NOT NULL,
            algorithm_id text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_search_events_workspace_id UNIQUE (workspace_id, id),
            CONSTRAINT ck_search_events_query_not_empty CHECK (btrim(query) <> ''),
            CONSTRAINT ck_search_events_parameters_object
                CHECK (jsonb_typeof(search_parameters) = 'object'),
            CONSTRAINT ck_search_events_result_snapshot_array
                CHECK (jsonb_typeof(result_snapshot) = 'array'),
            CONSTRAINT ck_search_events_memory_ids_array
                CHECK (jsonb_typeof(memory_ids) = 'array'),
            CONSTRAINT ck_search_events_document_ids_array
                CHECK (jsonb_typeof(document_ids) = 'array')
        )
        """
    )
    op.execute(
        f"CREATE INDEX ix_search_events_workspace_created "
        f"ON {EVENTS_TABLE} (workspace_id, created_at, id)"
    )
    op.execute(
        f"CREATE INDEX ix_search_events_requester_created "
        f"ON {EVENTS_TABLE} (requester_user_id, created_at)"
    )

    op.execute(
        f"""
        CREATE TABLE {RATINGS_TABLE} (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
            search_event_id uuid NOT NULL,
            memory_id uuid NOT NULL REFERENCES memories(id) ON DELETE RESTRICT,
            result_rank integer NOT NULL,
            rater_user_id uuid NULL REFERENCES users(id) ON DELETE RESTRICT,
            rater_pseudonym text NULL,
            rating_source text NOT NULL,
            allocation numeric(7, 4) NULL,
            exclusion_reason text NULL,
            idempotency_key uuid NOT NULL,
            recorded_by_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_search_ratings_event_workspace
                FOREIGN KEY (workspace_id, search_event_id)
                REFERENCES {EVENTS_TABLE} (workspace_id, id) ON DELETE RESTRICT,
            CONSTRAINT uq_search_ratings_workspace_idempotency
                UNIQUE (workspace_id, idempotency_key),
            CONSTRAINT ck_search_ratings_rank_positive CHECK (result_rank > 0),
            CONSTRAINT ck_search_ratings_source
                CHECK (rating_source IN ('independent', 'self', 'external')),
            CONSTRAINT ck_search_ratings_allocation_range
                CHECK (allocation IS NULL OR (allocation >= 0 AND allocation <= 100)),
            CONSTRAINT ck_search_ratings_value_or_exclusion
                CHECK ((allocation IS NOT NULL) <> (exclusion_reason IS NOT NULL)),
            CONSTRAINT ck_search_ratings_rater_identity
                CHECK (
                    (rating_source = 'external'
                        AND rater_user_id IS NULL
                        AND NULLIF(btrim(rater_pseudonym), '') IS NOT NULL)
                    OR
                    (rating_source IN ('independent', 'self')
                        AND rater_user_id IS NOT NULL
                        AND rater_pseudonym IS NULL)
                )
        )
        """
    )
    op.execute(
        f"CREATE INDEX ix_search_ratings_workspace_created "
        f"ON {RATINGS_TABLE} (workspace_id, created_at, id)"
    )
    op.execute(
        f"CREATE INDEX ix_search_ratings_event_memory "
        f"ON {RATINGS_TABLE} (search_event_id, memory_id)"
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_search_evidence_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'Pilot search evidence is append-only';
        END;
        $$
        """
    )
    for table in (EVENTS_TABLE, RATINGS_TABLE):
        op.execute(
            f"CREATE TRIGGER {table}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_search_evidence_mutation()"
        )

    events_member = _active_access(f"{EVENTS_TABLE}.workspace_id")
    events_insert = (
        f"({events_member}) AND {EVENTS_TABLE}.requester_user_id = {_USER_ID}"
    )
    ratings_admin = _admin_access(f"{RATINGS_TABLE}.workspace_id")

    for table in (EVENTS_TABLE, RATINGS_TABLE):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY sm_search_events_select ON {EVENTS_TABLE} "
        f"FOR SELECT USING ({events_member})"
    )
    op.execute(
        f"CREATE POLICY sm_search_events_insert ON {EVENTS_TABLE} "
        f"FOR INSERT WITH CHECK ({events_insert})"
    )
    op.execute(
        f"CREATE POLICY sm_search_ratings_select ON {RATINGS_TABLE} "
        f"FOR SELECT USING ({ratings_admin})"
    )
    op.execute(
        f"CREATE POLICY sm_search_ratings_insert ON {RATINGS_TABLE} "
        f"FOR INSERT WITH CHECK ({ratings_admin})"
    )

    for table in (EVENTS_TABLE, RATINGS_TABLE):
        grant_runtime_privileges(op.execute, role, table, _table_owner(table))


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM {EVENTS_TABLE} LIMIT 1)
               OR EXISTS (SELECT 1 FROM {RATINGS_TABLE} LIMIT 1) THEN
                RAISE EXCEPTION
                    'Cannot downgrade 20261010_0011 while pilot search evidence exists';
            END IF;
        END
        $$
        """
    )
    op.execute(f"DROP TABLE {RATINGS_TABLE}")
    op.execute(f"DROP TABLE {EVENTS_TABLE}")
    op.execute("DROP FUNCTION prevent_search_evidence_mutation()")
