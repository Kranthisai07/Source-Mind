"""Enforce active membership and tenant RLS across all workspace data.

Revision ID: 20260908_0006
Revises: 20250817_0005
Create Date: 2026-09-08
"""
# ruff: noqa: S608

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from alembic import op

revision = "20260908_0006"
down_revision = "20250817_0005"
branch_labels = None
depends_on = None

_USER_ID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_WORKSPACE_ID = "NULLIF(current_setting('app.current_workspace_id', true), '')::uuid"
_LEGACY_DIRECT_TABLES = ("documents", "memories", "memory_conflicts")
_NEW_RLS_TABLES = (
    "workspace_members",
    "connector_configs",
    "artifact_links",
    "handoff_records",
    "connector_sync_logs",
    "handoff_assignments",
)


def _assert_legacy_connector_compatibility() -> None:
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
            "Cannot downgrade to 20250817_0005 while connector_configs contains "
            "connector types unsupported by 0005. Preserve the upgraded "
            "database; do not delete or relabel connector data."
        ) from exc
    bind.execute(
        text(
            "ALTER TABLE connector_configs "
            "DROP CONSTRAINT ck_connector_configs_0005_preflight"
        )
    )


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


def _replace_policy(table: str, expression: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_workspace_isolation ON {table}")
    op.execute(f"DROP POLICY IF EXISTS sm_tenant_isolation ON {table}")
    op.execute(
        f"""
        CREATE POLICY sm_tenant_isolation ON {table}
        USING ({expression})
        WITH CHECK ({expression})
        """
    )


def _restore_legacy_direct_policy(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_workspace_isolation ON {table}
        USING (
            workspace_id = current_setting('app.current_workspace_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'true'
        )
        """
    )


def _restore_legacy_memory_policy(table: str, memory_column: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_workspace_isolation ON {table}
        USING (
            EXISTS (
                SELECT 1 FROM memories m
                WHERE m.id = {table}.{memory_column}
                  AND (
                    m.workspace_id =
                        current_setting('app.current_workspace_id', true)::uuid
                    OR current_setting('app.bypass_rls', true) = 'true'
                  )
            )
        )
        """
    )


def upgrade() -> None:
    op.create_check_constraint(
        "ck_workspace_members_role",
        "workspace_members",
        "role IN ('owner', 'admin', 'member', 'viewer')",
    )
    op.create_check_constraint(
        "ck_workspace_members_status",
        "workspace_members",
        "status IN ('active', 'departing', 'departed')",
    )

    op.drop_constraint("ck_connector_configs_type", "connector_configs", type_="check")
    op.create_check_constraint(
        "ck_connector_configs_type",
        "connector_configs",
        "connector_type IN ('github', 'discord', 'slack', 'notion')",
    )

    op.execute("ALTER TABLE workspace_members ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspace_members FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS sm_workspace_members_isolation ON workspace_members")
    op.execute(
        f"""
        CREATE POLICY sm_workspace_members_isolation ON workspace_members
        USING (
            (user_id = {_USER_ID} AND status = 'active' AND departed_at IS NULL)
            OR workspace_id = {_WORKSPACE_ID}
        )
        WITH CHECK (
            user_id = {_USER_ID}
            OR workspace_id = {_WORKSPACE_ID}
        )
        """
    )

    op.execute("ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspaces FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS workspace_isolation ON workspaces")
    op.execute("DROP POLICY IF EXISTS sm_workspace_isolation ON workspaces")
    op.execute(
        f"""
        CREATE POLICY sm_workspace_isolation ON workspaces
        USING (
            deleted_at IS NULL
            AND (
                id = {_WORKSPACE_ID}
                OR EXISTS (
                    SELECT 1 FROM workspace_members AS own_membership
                    WHERE own_membership.workspace_id = workspaces.id
                      AND own_membership.user_id = {_USER_ID}
                      AND own_membership.status = 'active'
                      AND own_membership.departed_at IS NULL
                )
            )
        )
        WITH CHECK ({_USER_ID} IS NOT NULL)
        """
    )

    for table in (
        "documents",
        "memories",
        "memory_conflicts",
        "connector_configs",
        "artifact_links",
        "handoff_records",
    ):
        _replace_policy(table, _active_access(f"{table}.workspace_id"))

    _replace_policy(
        "attributions",
        f"""
        EXISTS (
            SELECT 1 FROM memories AS tenant_memory
            WHERE tenant_memory.id = attributions.memory_id
              AND {_active_access('tenant_memory.workspace_id')}
        )
        """,
    )
    _replace_policy(
        "attribution_edits",
        f"""
        EXISTS (
            SELECT 1 FROM memories AS tenant_memory
            WHERE tenant_memory.id = attribution_edits.memory_id
              AND {_active_access('tenant_memory.workspace_id')}
        )
        """,
    )
    _replace_policy(
        "memory_relations",
        f"""
        EXISTS (
            SELECT 1
            FROM memories AS source_memory
            JOIN memories AS target_memory
              ON target_memory.id = memory_relations.target_memory_id
            WHERE source_memory.id = memory_relations.source_memory_id
              AND source_memory.workspace_id = target_memory.workspace_id
              AND {_active_access('source_memory.workspace_id')}
        )
        """,
    )
    _replace_policy(
        "connector_sync_logs",
        f"""
        EXISTS (
            SELECT 1 FROM connector_configs AS tenant_connector
            WHERE tenant_connector.id = connector_sync_logs.connector_id
              AND {_active_access('tenant_connector.workspace_id')}
        )
        """,
    )
    _replace_policy(
        "handoff_assignments",
        f"""
        EXISTS (
            SELECT 1 FROM handoff_records AS tenant_handoff
            WHERE tenant_handoff.id = handoff_assignments.handoff_id
              AND {_active_access('tenant_handoff.workspace_id')}
        )
        """,
    )


def downgrade() -> None:
    _assert_legacy_connector_compatibility()

    for table in (
        "handoff_assignments",
        "connector_sync_logs",
        "memory_relations",
        "attribution_edits",
        "attributions",
        "handoff_records",
        "artifact_links",
        "connector_configs",
        "memory_conflicts",
        "memories",
        "documents",
    ):
        op.execute(f"DROP POLICY IF EXISTS sm_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")

    for table in _LEGACY_DIRECT_TABLES:
        _restore_legacy_direct_policy(table)
    _restore_legacy_memory_policy("attributions", "memory_id")
    _restore_legacy_memory_policy("attribution_edits", "memory_id")
    _restore_legacy_memory_policy("memory_relations", "source_memory_id")

    op.execute("ALTER TABLE workspace_members NO FORCE ROW LEVEL SECURITY")
    for table in _NEW_RLS_TABLES:
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS sm_workspace_isolation ON workspaces")
    op.execute("ALTER TABLE workspaces NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY workspace_isolation ON workspaces
        USING (
            id = current_setting('app.current_workspace_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'true'
        )
        """
    )
    op.execute("DROP POLICY IF EXISTS sm_workspace_members_isolation ON workspace_members")

    op.drop_constraint("ck_connector_configs_type", "connector_configs", type_="check")
    op.create_check_constraint(
        "ck_connector_configs_type",
        "connector_configs",
        "connector_type IN ('github', 'discord')",
    )
    op.drop_constraint("ck_workspace_members_status", "workspace_members", type_="check")
    op.drop_constraint("ck_workspace_members_role", "workspace_members", type_="check")
