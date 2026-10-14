"""Schema for automatic, recoverable source ingestion (additive).

Revision ID: 20261015_0013
Revises: 20261014_0012
Create Date: 2026-10-15

Everything here is additive and defaulted, so the previous API and worker keep
working against the migrated schema (rolling-release order: migrate, worker,
API). The migration is never downgraded in production; the downgrade exists for
local round-trip tests and refuses to run once any new table holds rows or a
new column is in use.

connector_configs   installation binding, lease + fence (stale-worker rejection),
                    disconnect generation, per-connector schedule, new statuses.
connector_sync_logs trigger, updated/failed/filtered counters, structured code.
connector_sync_cursors  per (connector, repo, artifact_type) discovery position.
connector_work_items    handoff attempts and quarantine (never a silent skip).
artifact_heads      per-artifact revision counters and the published revision.
documents           supersession link and the connector generation stamp.
artifact_links      connector generation stamp; the pending-anchor uniqueness now
                    includes the revision so a new revision can be handed off while
                    an older anchor is still un-backfilled.

Every new table carries workspace_id, FORCE ROW LEVEL SECURITY with the same
active-membership rule the other workspace tables use, and explicit runtime
privileges (no reliance on default privileges).

The periodic scheduler has no user to impersonate and must list due connector ids
across workspaces. Following the project's existing pattern for privileged reads
(``sm_workspace_access_grants`` + SECURITY DEFINER functions), a trigger keeps
the internal table ``sm_connector_schedule`` (ids, status and due times only; no
RLS, no runtime privileges) in step with connector_configs, and the runtime role
may only EXECUTE ``sm_list_due_connectors(limit)``. No policy on any protected
table is conditioned on anything but active workspace membership.
"""
# ruff: noqa: S608

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any

from sqlalchemy import text

from alembic import context, op

revision = "20261015_0013"
down_revision = "20261014_0012"
branch_labels = None
depends_on = None

RUNTIME_ROLE_ENV = "SOURCEMIND_RUNTIME_ROLE"
RUNTIME_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"
NEW_TABLES = ("connector_sync_cursors", "connector_work_items", "artifact_heads")

_ROLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_USER_ID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_WORKSPACE_ID = "NULLIF(current_setting('app.current_workspace_id', true), '')::uuid"

CONNECTOR_STATUSES = "('active','paused','error','disconnected','paused_budget')"
SYNC_LOG_STATUSES = "('running','completed','failed','continuing','interrupted','aborted')"
SYNC_TRIGGERS = "('manual','scheduled','recovery')"
SCOPE_STATUSES = (
    "('ok','access_lost','authorization_unverified','reauthorization_required','error')"
)
SCOPE_PHASES = "('backfill','incremental')"
WORK_STATES = "('pending','quarantined','done')"


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
    """The same rule restricted to owners and admins (the roles that may change these tables)."""
    return _active_access(workspace_expression).rstrip().rstrip(")") + """
              AND access_membership.role IN ('owner', 'admin')
        )
    """


def required_runtime_role() -> str:
    role = (os.environ.get(RUNTIME_ROLE_ENV) or "").strip()
    if not role:
        raise RuntimeError(
            f"{RUNTIME_ROLE_ENV} must name the restricted runtime database role "
            "before creating the automatic-ingestion tables."
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


def _visible_count(table: str, where: str = "true") -> int:
    """Row count independent of FORCE RLS (the migration role owns the table).

    Under FORCE ROW LEVEL SECURITY even the owner is filtered, so a guard that
    counts rows without this would silently see zero when run by a role without
    BYPASSRLS. The toggle is transactional and restored before returning.
    """
    conn = op.get_bind()
    conn.exec_driver_sql(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    try:
        return int(conn.exec_driver_sql(f"SELECT count(*) FROM {table} WHERE {where}").scalar() or 0)
    finally:
        conn.exec_driver_sql(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def _create_schedule_index(role: str) -> None:
    """Internal schedule table, its sync trigger and the due-connector function."""
    op.execute(
        """
        CREATE TABLE sm_connector_schedule (
            connector_id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL,
            created_by uuid,
            status text NOT NULL,
            next_sync_at timestamptz,
            sync_lease_expires_at timestamptz
        )
        """
    )
    op.execute("REVOKE ALL ON TABLE sm_connector_schedule FROM PUBLIC")
    # In a single-role setup the runtime role owns the table; revoking from the owner would strip
    # its own privileges and break the trigger and the backfill below (same rule as
    # grant_runtime_privileges and migration 0010).
    if _table_owner("sm_connector_schedule") != role:
        op.execute(f'REVOKE ALL ON TABLE sm_connector_schedule FROM "{role}"')
    op.execute(
        """
        CREATE FUNCTION sm_sync_connector_schedule() RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                DELETE FROM public.sm_connector_schedule WHERE connector_id = OLD.id;
                RETURN OLD;
            END IF;
            INSERT INTO public.sm_connector_schedule AS s
                (connector_id, workspace_id, created_by, status, next_sync_at,
                 sync_lease_expires_at)
            VALUES (NEW.id, NEW.workspace_id, NEW.created_by, NEW.status, NEW.next_sync_at,
                    NEW.sync_lease_expires_at)
            ON CONFLICT (connector_id) DO UPDATE SET
                workspace_id = EXCLUDED.workspace_id,
                created_by = EXCLUDED.created_by,
                status = EXCLUDED.status,
                next_sync_at = EXCLUDED.next_sync_at,
                sync_lease_expires_at = EXCLUDED.sync_lease_expires_at;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION sm_sync_connector_schedule() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER sm_connector_schedule_sync "
        "AFTER INSERT OR DELETE OR UPDATE OF workspace_id, created_by, status, next_sync_at, "
        "sync_lease_expires_at ON connector_configs "
        "FOR EACH ROW EXECUTE FUNCTION sm_sync_connector_schedule()"
    )
    op.execute(
        """
        CREATE FUNCTION sm_list_due_connectors(max_rows integer)
        RETURNS TABLE (
            connector_id uuid, workspace_id uuid, created_by uuid, creator_active boolean,
            acting_admin uuid
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT s.connector_id, s.workspace_id, s.created_by,
                   EXISTS (
                       SELECT 1
                       FROM public.sm_workspace_access_grants AS m
                       JOIN public.sm_workspace_bootstraps AS w
                         ON w.workspace_id = m.workspace_id
                       WHERE m.workspace_id = s.workspace_id
                         AND m.user_id = s.created_by
                         AND m.status = 'active'
                         AND m.departed_at IS NULL
                         AND m.role IN ('owner', 'admin')
                         AND w.deleted_at IS NULL
                   ) AS creator_active,
                   -- An active owner/admin whose context may be used to RECORD (not perform)
                   -- an owner_inactive outcome when the creator has left; NULL = nobody left.
                   (
                       SELECT g.user_id
                       FROM public.sm_workspace_access_grants AS g
                       JOIN public.sm_workspace_bootstraps AS b
                         ON b.workspace_id = g.workspace_id
                       WHERE g.workspace_id = s.workspace_id
                         AND g.status = 'active'
                         AND g.departed_at IS NULL
                         AND g.role IN ('owner', 'admin')
                         AND b.deleted_at IS NULL
                       ORDER BY (g.role = 'owner') DESC, g.user_id
                       LIMIT 1
                   ) AS acting_admin
            FROM public.sm_connector_schedule AS s
            WHERE s.status = 'active'
              AND (s.next_sync_at IS NULL OR s.next_sync_at <= now())
              AND (s.sync_lease_expires_at IS NULL OR s.sync_lease_expires_at < now())
              -- A connector nobody can act for (workspace deleted, or no owner/admin left) can
              -- never be rescheduled by the tick, so it would stay at the front of the queue
              -- forever and starve healthy connectors.
              AND EXISTS (
                  SELECT 1
                  FROM public.sm_workspace_access_grants AS a
                  JOIN public.sm_workspace_bootstraps AS wb ON wb.workspace_id = a.workspace_id
                  WHERE a.workspace_id = s.workspace_id
                    AND a.status = 'active'
                    AND a.departed_at IS NULL
                    AND a.role IN ('owner', 'admin')
                    AND wb.deleted_at IS NULL
              )
            ORDER BY s.next_sync_at NULLS FIRST, s.connector_id
            LIMIT GREATEST(1, LEAST(max_rows, 500))
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION sm_list_due_connectors(integer) FROM PUBLIC")
    op.execute(f'GRANT EXECUTE ON FUNCTION sm_list_due_connectors(integer) TO "{role}"')
    # Backfill existing connectors (FORCE RLS is lifted for the owner only inside
    # this transaction; connectors created before this migration must be scheduled).
    op.execute("ALTER TABLE connector_configs NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "INSERT INTO sm_connector_schedule "
        "(connector_id, workspace_id, created_by, status, next_sync_at, sync_lease_expires_at) "
        "SELECT id, workspace_id, created_by, status, next_sync_at, sync_lease_expires_at "
        "FROM connector_configs"
    )
    op.execute("ALTER TABLE connector_configs FORCE ROW LEVEL SECURITY")


def sweep_other_grantees(execute: Callable[[str], Any], role: str) -> None:
    """Revoke every grant on the new objects from roles other than the owner and the runtime role.

    Platforms that set default privileges (e.g. Supabase grants new public tables and functions to
    ``anon``/``authenticated``/``service_role``) would otherwise leave those roles able to read the
    unprotected schedule table (connector, workspace and creator ids across tenants) or call the
    cross-tenant due-list function. Same sweep migration 0007 applies to its internal tables.
    """
    tables = ", ".join(f"'{t}'" for t in (*NEW_TABLES, "sm_connector_schedule"))
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
                WHERE c.relname IN ({tables})
                  AND a.grantee <> 0 AND a.grantee <> c.relowner
                  AND pg_get_userbyid(a.grantee) <> '{role}'
            LOOP
                EXECUTE format('REVOKE ALL ON TABLE public.%I FROM %I', r.relname, r.grantee_name);
            END LOOP;
            FOR r IN
                SELECT p.oid::regprocedure::text AS proc, pg_get_userbyid(a.grantee) AS grantee_name
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace AND n.nspname = 'public'
                CROSS JOIN LATERAL aclexplode(p.proacl) a
                WHERE p.proname IN ('sm_list_due_connectors', 'sm_sync_connector_schedule')
                  AND a.grantee <> 0 AND a.grantee <> p.proowner
                  AND (pg_get_userbyid(a.grantee) <> '{role}'
                       OR p.proname = 'sm_sync_connector_schedule')
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

    # ── connector_configs ────────────────────────────────────────────────────
    op.execute(
        """
        ALTER TABLE connector_configs
            ADD COLUMN IF NOT EXISTS installation_id bigint,
            ADD COLUMN IF NOT EXISTS authorized_github_user_id bigint,
            ADD COLUMN IF NOT EXISTS created_by uuid
                REFERENCES users(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS sync_interval_minutes integer NOT NULL DEFAULT 15,
            ADD COLUMN IF NOT EXISTS sync_lease_owner text,
            ADD COLUMN IF NOT EXISTS sync_lease_expires_at timestamptz,
            ADD COLUMN IF NOT EXISTS fence bigint NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS generation integer NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS last_success_at timestamptz,
            ADD COLUMN IF NOT EXISTS last_error_code text
        """
    )
    op.execute(
        "ALTER TABLE connector_configs "
        "ADD CONSTRAINT ck_connector_configs_interval "
        "CHECK (sync_interval_minutes BETWEEN 5 AND 1440)"
    )
    op.execute("ALTER TABLE connector_configs DROP CONSTRAINT ck_connector_configs_status")
    op.execute(
        "ALTER TABLE connector_configs ADD CONSTRAINT ck_connector_configs_status "
        f"CHECK (status IN {CONNECTOR_STATUSES})"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_connector_configs_due "
        "ON connector_configs (next_sync_at) WHERE status = 'active'"
    )
    _create_schedule_index(role)

    # ── connector_sync_logs ──────────────────────────────────────────────────
    op.execute(
        """
        ALTER TABLE connector_sync_logs
            ADD COLUMN IF NOT EXISTS trigger text NOT NULL DEFAULT 'manual',
            ADD COLUMN IF NOT EXISTS artifacts_updated integer NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS artifacts_failed integer NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS artifacts_filtered jsonb NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN IF NOT EXISTS error_code text
        """
    )
    op.execute(
        "ALTER TABLE connector_sync_logs ADD CONSTRAINT ck_sync_logs_trigger "
        f"CHECK (trigger IN {SYNC_TRIGGERS})"
    )
    op.execute("ALTER TABLE connector_sync_logs DROP CONSTRAINT ck_sync_logs_status")
    op.execute(
        "ALTER TABLE connector_sync_logs ADD CONSTRAINT ck_sync_logs_status "
        f"CHECK (status IN {SYNC_LOG_STATUSES})"
    )

    # ── documents / artifact_links ───────────────────────────────────────────
    op.execute(
        """
        ALTER TABLE documents
            ADD COLUMN IF NOT EXISTS supersedes_document_id uuid
                REFERENCES documents(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS connector_id uuid,
            ADD COLUMN IF NOT EXISTS connector_generation integer
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_documents_supersedes "
        "ON documents (supersedes_document_id) WHERE supersedes_document_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_documents_connector "
        "ON documents (connector_id) WHERE connector_id IS NOT NULL"
    )
    op.execute("ALTER TABLE artifact_links ADD COLUMN IF NOT EXISTS connector_generation integer")
    # One pending (un-backfilled) anchor per artifact REVISION, not per artifact.
    op.execute("DROP INDEX IF EXISTS uq_artifact_links_pending")
    op.execute(
        "CREATE UNIQUE INDEX uq_artifact_links_pending ON artifact_links "
        "(workspace_id, source_tool, source_type, source_id, revision_seq) "
        "WHERE memory_id IS NULL"
    )

    # ── connector_sync_cursors ───────────────────────────────────────────────
    op.execute(
        f"""
        CREATE TABLE connector_sync_cursors (
            connector_id uuid NOT NULL REFERENCES connector_configs(id) ON DELETE CASCADE,
            workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            repo text NOT NULL,
            artifact_type text NOT NULL,
            phase text NOT NULL DEFAULT 'backfill',
            window_low timestamptz,
            window_high timestamptz,
            resume_ts timestamptz,
            resume_id text,
            listing_complete boolean NOT NULL DEFAULT false,
            head_sha text,
            consumed integer NOT NULL DEFAULT 0,
            status text NOT NULL DEFAULT 'ok',
            last_error_code text,
            authorization_verified_at timestamptz,
            last_progress_at timestamptz,
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (connector_id, repo, artifact_type),
            CONSTRAINT ck_sync_cursors_phase CHECK (phase IN {SCOPE_PHASES}),
            CONSTRAINT ck_sync_cursors_status CHECK (status IN {SCOPE_STATUSES})
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_sync_cursors_workspace ON connector_sync_cursors (workspace_id)"
    )

    # ── connector_work_items ─────────────────────────────────────────────────
    op.execute(
        f"""
        CREATE TABLE connector_work_items (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            connector_id uuid NOT NULL REFERENCES connector_configs(id) ON DELETE CASCADE,
            workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            repo text NOT NULL,
            artifact_type text NOT NULL,
            source_id text NOT NULL,
            attempts integer NOT NULL DEFAULT 0,
            state text NOT NULL DEFAULT 'pending',
            last_error_code text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_work_items_state CHECK (state IN {WORK_STATES}),
            CONSTRAINT uq_work_items_identity
                UNIQUE (connector_id, repo, artifact_type, source_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_work_items_workspace ON connector_work_items (workspace_id)"
    )

    # ── artifact_heads ───────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE artifact_heads (
            workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            source_tool text NOT NULL,
            source_type text NOT NULL,
            source_id text NOT NULL,
            latest_seq integer NOT NULL DEFAULT 0,
            published_seq integer NOT NULL DEFAULT 0,
            published_document_id uuid REFERENCES documents(id) ON DELETE SET NULL,
            missing_scans integer NOT NULL DEFAULT 0,
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, source_tool, source_type, source_id),
            CONSTRAINT ck_artifact_heads_seq CHECK (
                published_seq >= 0 AND latest_seq >= published_seq
            )
        )
        """
    )

    # ── RLS + explicit runtime privileges for every new table ────────────────
    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS sm_{table}_isolation ON {table}")
        # Reading is open to active members, like the tables these reference. WRITING and LOCKING
        # (SELECT ... FOR UPDATE evaluates the UPDATE policy's USING) are owner/admin only: the only
        # code that touches these rows is a sync or a publication run on behalf of the connector's
        # administrator, so a viewer or plain member gets nothing, not even a row lock that could
        # stall a publication, from a compromised or mistaken runtime path.
        member = _active_access(f"{table}.workspace_id")
        admin = _admin_access(f"{table}.workspace_id")
        op.execute(f"CREATE POLICY sm_{table}_select ON {table} FOR SELECT USING ({member})")
        op.execute(f"CREATE POLICY sm_{table}_insert ON {table} FOR INSERT WITH CHECK ({admin})")
        op.execute(
            f"CREATE POLICY sm_{table}_update ON {table} "
            f"FOR UPDATE USING ({admin}) WITH CHECK ({admin})"
        )
        op.execute(f"CREATE POLICY sm_{table}_delete ON {table} FOR DELETE USING ({admin})")
        grant_runtime_privileges(op.execute, role, table, _table_owner(table))
    sweep_other_grantees(op.execute, role)


def downgrade() -> None:
    used = 0
    for table in NEW_TABLES:
        used += _visible_count(table)
    used += _visible_count(
        "connector_configs",
        "installation_id IS NOT NULL OR created_by IS NOT NULL OR generation <> 0 "
        "OR fence <> 0 OR status IN ('disconnected','paused_budget')",
    )
    used += _visible_count(
        "documents", "supersedes_document_id IS NOT NULL OR connector_id IS NOT NULL"
    )
    used += _visible_count(
        "connector_sync_logs",
        "trigger <> 'manual' OR status IN ('continuing','interrupted','aborted')",
    )
    if used:
        raise RuntimeError(
            "Refusing to downgrade 20261015_0013: automatic-ingestion state is in use."
        )
    for table in NEW_TABLES:
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP INDEX IF EXISTS uq_artifact_links_pending")
    op.execute(
        "CREATE UNIQUE INDEX uq_artifact_links_pending ON artifact_links "
        "(workspace_id, source_tool, source_type, source_id) WHERE memory_id IS NULL"
    )
    op.execute("ALTER TABLE artifact_links DROP COLUMN IF EXISTS connector_generation")
    op.execute("DROP INDEX IF EXISTS ix_documents_connector")
    op.execute("DROP INDEX IF EXISTS ix_documents_supersedes")
    op.execute(
        "ALTER TABLE documents DROP COLUMN IF EXISTS connector_generation, "
        "DROP COLUMN IF EXISTS connector_id, DROP COLUMN IF EXISTS supersedes_document_id"
    )
    op.execute("ALTER TABLE connector_sync_logs DROP CONSTRAINT ck_sync_logs_status")
    op.execute(
        "ALTER TABLE connector_sync_logs ADD CONSTRAINT ck_sync_logs_status "
        "CHECK (status IN ('running', 'completed', 'failed'))"
    )
    op.execute("ALTER TABLE connector_sync_logs DROP CONSTRAINT ck_sync_logs_trigger")
    op.execute(
        "ALTER TABLE connector_sync_logs DROP COLUMN IF EXISTS error_code, "
        "DROP COLUMN IF EXISTS artifacts_filtered, DROP COLUMN IF EXISTS artifacts_failed, "
        "DROP COLUMN IF EXISTS artifacts_updated, DROP COLUMN IF EXISTS trigger"
    )
    op.execute("DROP TRIGGER IF EXISTS sm_connector_schedule_sync ON connector_configs")
    op.execute("DROP FUNCTION IF EXISTS sm_list_due_connectors(integer)")
    op.execute("DROP FUNCTION IF EXISTS sm_sync_connector_schedule()")
    op.execute("DROP TABLE IF EXISTS sm_connector_schedule")
    op.execute("DROP INDEX IF EXISTS ix_connector_configs_due")
    op.execute("ALTER TABLE connector_configs DROP CONSTRAINT ck_connector_configs_status")
    op.execute(
        "ALTER TABLE connector_configs ADD CONSTRAINT ck_connector_configs_status "
        "CHECK (status IN ('active', 'paused', 'error'))"
    )
    op.execute("ALTER TABLE connector_configs DROP CONSTRAINT ck_connector_configs_interval")
    op.execute(
        "ALTER TABLE connector_configs DROP COLUMN IF EXISTS last_error_code, "
        "DROP COLUMN IF EXISTS last_success_at, DROP COLUMN IF EXISTS generation, "
        "DROP COLUMN IF EXISTS fence, DROP COLUMN IF EXISTS sync_lease_expires_at, "
        "DROP COLUMN IF EXISTS sync_lease_owner, DROP COLUMN IF EXISTS sync_interval_minutes, "
        "DROP COLUMN IF EXISTS created_by, DROP COLUMN IF EXISTS authorized_github_user_id, "
        "DROP COLUMN IF EXISTS installation_id"
    )
