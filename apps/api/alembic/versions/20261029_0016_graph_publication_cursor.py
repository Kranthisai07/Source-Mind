"""Per-workspace publication cursor, so the API can tell a fresh graph from a lagging one.

Revision ID: 20261029_0016
Revises: 20261029_0015
Create Date: 2026-10-29

The relay upserts one row per workspace, in the same transaction that marks outbox rows
published, with the highest ``seq`` it has published for that workspace. The projector records
the highest ``seq`` it has applied on the workspace node in Neo4j. A graph read is served from
the graph only when applied >= published; otherwise PostgreSQL answers.

The API's runtime role needs the number but must not read the outbox (which carries event
payloads for every workspace), hence a separate, tiny table. Like the outbox it is protected by
grants rather than row-level security, so the relay needs no policy bypass: the runtime role may
only SELECT, and the optional relay role (SOURCEMIND_GRAPH_RELAY_ROLE) may SELECT, INSERT and
UPDATE (published_seq, updated_at). The API only ever asks about the workspace it has already
authorised. It holds a counter, never content.
"""
# ruff: noqa: S608

from __future__ import annotations

import os
import re

from sqlalchemy import text

from alembic import context, op

revision = "20261029_0016"
down_revision = "20261029_0015"
branch_labels = None
depends_on = None

RUNTIME_ROLE_ENV = "SOURCEMIND_RUNTIME_ROLE"
RELAY_ROLE_ENV = "SOURCEMIND_GRAPH_RELAY_ROLE"
TABLE = "graph_publication_cursor"

_ROLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _role_from_env(name: str, *, required: bool) -> str | None:
    role = (os.environ.get(name) or "").strip()
    if not role:
        if required:
            raise RuntimeError(
                f"{name} must name the restricted runtime database role "
                "(production: sourcemind_runtime; CI and disposable test databases: "
                f"sourcemind_test) so this migration can grant it access to {TABLE}."
            )
        return None
    if not _ROLE_NAME.fullmatch(role):
        raise RuntimeError(f"{name} must be a plain PostgreSQL role identifier.")
    return role


def _assert_role_exists(name: str, role: str) -> None:
    if context.is_offline_mode():
        return
    exists = (
        op.get_bind()
        .execute(text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role})
        .scalar()
    )
    if not exists:
        raise RuntimeError(f"{name} names role {role!r}, which does not exist in this cluster.")


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


def upgrade() -> None:
    runtime_role = _role_from_env(RUNTIME_ROLE_ENV, required=True)
    relay_role = _role_from_env(RELAY_ROLE_ENV, required=False)
    assert runtime_role is not None
    _assert_role_exists(RUNTIME_ROLE_ENV, runtime_role)
    if relay_role is not None:
        _assert_role_exists(RELAY_ROLE_ENV, relay_role)

    op.execute(
        f"""
        CREATE TABLE {TABLE} (
            workspace_id uuid PRIMARY KEY,
            published_seq bigint NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(f"REVOKE ALL ON TABLE {TABLE} FROM PUBLIC")
    owner = _table_owner()
    if runtime_role != owner:
        op.execute(f'REVOKE ALL ON TABLE {TABLE} FROM "{runtime_role}"')
        op.execute(f'GRANT SELECT ON TABLE {TABLE} TO "{runtime_role}"')
    if relay_role is not None and relay_role != owner:
        op.execute(f'REVOKE ALL ON TABLE {TABLE} FROM "{relay_role}"')
        op.execute(f'GRANT SELECT, INSERT ON TABLE {TABLE} TO "{relay_role}"')
        op.execute(
            f'GRANT UPDATE (published_seq, updated_at) ON TABLE {TABLE} TO "{relay_role}"'
        )


def downgrade() -> None:
    # A counter that the relay rebuilds as it publishes: nothing here is worth refusing a rollback.
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
