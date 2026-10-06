"""Admin-asserted GitHub author links with explicit runtime grants.

Revision ID: 20261005_0010
Revises: 20260916_0009
Create Date: 2026-10-05

D-021. Adds ``github_author_links``: a workspace-scoped, admin-asserted map
from GitHub's NUMERIC user id to a workspace member, used to credit synced
GitHub artifacts to their real author instead of the sync initiator.

Grants are explicit. No earlier migration grants to a runtime role; CI masked
that through ALTER DEFAULT PRIVILEGES, while production grants an exact table
allowlist. The runtime role is named by the REQUIRED environment variable
SOURCEMIND_RUNTIME_ROLE and the upgrade aborts, before any DDL, when it is
unset, malformed or names a role that does not exist. Shipping a table the
runtime cannot read would make every lookup fail closed as "unresolved".
If the named role is the table owner (a single-role local setup) the
revoke/grant for it is skipped: revoking from the owner would strip its own
TRUNCATE/REFERENCES/TRIGGER.

Row-level security, per command:
  SELECT  member-level: the 0006 active-membership expression.
  INSERT  WITH CHECK admin: the same expression plus
          role IN ('owner', 'admin').
  UPDATE  USING member-level, WITH CHECK admin. USING stays member-level on
          purpose: SELECT ... FOR SHARE evaluates the UPDATE policy's USING,
          and the ingestion worker runs as the document submitter (a
          contributor), so it must be able to lock a link that it cannot
          change.
  DELETE  USING admin.

Exact limit of this enforcement: the database trusts the APPLICATION-DECLARED
user id. ``app.current_user_id`` is a transaction-local setting the API and
worker set with set_config(); it is not authentication. These policies make
writes admin-only for the declared identity. Any process that holds the
runtime role's credentials and can run arbitrary SQL can declare an admin's id
and write. Reads stay member-level. The API's ADMINISTER check is unchanged
and remains the primary control.
"""
# ruff: noqa: S608

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from alembic import context, op

revision = "20261005_0010"
down_revision = "20260916_0009"
branch_labels = None
depends_on = None

TABLE = "github_author_links"
RUNTIME_ROLE_ENV = "SOURCEMIND_RUNTIME_ROLE"
RUNTIME_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"

_ROLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_USER_ID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_WORKSPACE_ID = "NULLIF(current_setting('app.current_workspace_id', true), '')::uuid"

_LEGACY_CONNECTOR_TARGETS = {
    None,
    "0001_initial",
    "20250312_0002",
    "20250415_0003",
    "20250816_0004",
    "20250817_0005",
}


def _active_access(workspace_expression: str) -> str:
    """Verbatim copy of the 20260908_0006 tenant expression (tested equal)."""
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
    """The 0006 expression restricted to owners and admins of the workspace."""
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
    """Return the runtime role from the environment or abort the migration."""
    role = (os.environ.get(RUNTIME_ROLE_ENV) or "").strip()
    if not role:
        raise RuntimeError(
            f"{RUNTIME_ROLE_ENV} must name the restricted runtime database role "
            "(production: sourcemind_runtime; CI and disposable test databases: "
            "sourcemind_test) so this migration can grant it access to "
            f"{TABLE}. Set it and re-run `alembic upgrade head`."
        )
    if not _ROLE_NAME.fullmatch(role):
        raise RuntimeError(
            f"{RUNTIME_ROLE_ENV} must be a plain PostgreSQL role identifier "
            "(letters, digits, underscore; not starting with a digit)."
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
        raise RuntimeError(
            f"{RUNTIME_ROLE_ENV} names role {role!r}, which does not exist in this "
            "cluster. Create it (or correct the variable) before migrating."
        )


def grant_runtime_privileges(
    execute: Callable[[str], Any], role: str, table_owner: str | None = None
) -> None:
    """Make the table ACL exactly: owner + the four DML privileges for ``role``.

    Revokes first so the result does not depend on default privileges. No
    sequence grant is needed (uuid default) and TRUNCATE/REFERENCES/TRIGGER are
    deliberately not granted. ``role`` must already be validated. When
    ``role`` is the table owner it already holds every privilege, and revoking
    from it would strip its own TRUNCATE/REFERENCES/TRIGGER, so only PUBLIC is
    revoked.
    """
    execute(f"REVOKE ALL ON TABLE {TABLE} FROM PUBLIC")
    if table_owner is not None and role == table_owner:
        return
    execute(f'REVOKE ALL ON TABLE {TABLE} FROM "{role}"')
    execute(f'GRANT {RUNTIME_PRIVILEGES} ON TABLE {TABLE} TO "{role}"')


def _table_owner() -> str | None:
    if context.is_offline_mode():
        return None
    owner = (
        op.get_bind()
        .execute(
            text(
                "SELECT tableowner FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename = :table"
            ),
            {"table": TABLE},
        )
        .scalar()
    )
    return str(owner) if owner is not None else None


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
    role = required_runtime_role()
    _assert_role_exists(role)

    op.execute(
        f"""
        CREATE TABLE {TABLE} (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL
                REFERENCES workspaces(id) ON DELETE CASCADE,
            github_user_id bigint NOT NULL,
            github_login text NULL,
            user_id uuid NOT NULL
                REFERENCES users(id) ON DELETE CASCADE,
            created_by_user_id uuid NOT NULL
                REFERENCES users(id) ON DELETE RESTRICT,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_by_user_id uuid NULL
                REFERENCES users(id) ON DELETE RESTRICT,
            updated_at timestamptz NULL,
            CONSTRAINT ck_github_author_links_positive_id
                CHECK (github_user_id > 0),
            CONSTRAINT uq_github_author_links_ws_github_user
                UNIQUE (workspace_id, github_user_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX ix_github_author_links_ws_user ON {TABLE} (workspace_id, user_id)"
    )
    op.execute(
        f"COMMENT ON TABLE {TABLE} IS "
        "'Admin-asserted GitHub numeric user id -> workspace member links (D-021). "
        "Not proof of account ownership; github_login is a label only.'"
    )

    member = _active_access(f"{TABLE}.workspace_id")
    admin = _admin_access(f"{TABLE}.workspace_id")
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY sm_github_author_links_select ON {TABLE} "
        f"FOR SELECT USING ({member})"
    )
    op.execute(
        f"CREATE POLICY sm_github_author_links_insert ON {TABLE} "
        f"FOR INSERT WITH CHECK ({admin})"
    )
    op.execute(
        f"CREATE POLICY sm_github_author_links_update ON {TABLE} "
        f"FOR UPDATE USING ({member}) WITH CHECK ({admin})"
    )
    op.execute(
        f"CREATE POLICY sm_github_author_links_delete ON {TABLE} "
        f"FOR DELETE USING ({admin})"
    )

    grant_runtime_privileges(op.execute, role, _table_owner())


def downgrade() -> None:
    # transaction_per_migration=True commits each revision on its own. Without
    # this, a head -> 0005 rollback would commit this DROP before 0009's guard
    # refused the rollback (D-013), losing every link for nothing.
    if context.get_revision_argument() in _LEGACY_CONNECTOR_TARGETS:
        _assert_0005_rollback_compatibility()

    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
