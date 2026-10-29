"""Transactional outbox for the Neo4j graph projection.

Revision ID: 20261029_0015
Revises: 20261016_0014
Create Date: 2026-10-29

Rows are written in the same transaction as the domain change, so the graph
never sees a version Postgres rolled back and a crash between commit and publish
loses nothing. A separate relay process publishes them to Redpanda.

Access is by grants, not row-level security, so the relay (which spans every
workspace) never needs a policy bypass:

* the runtime role may only INSERT (and use the identity sequence);
* an optional relay role, named by SOURCEMIND_GRAPH_RELAY_ROLE, may SELECT and
  UPDATE (published_at) only. When the variable is unset the relay grants are
  skipped, which is the right state while the graph flags are off.

Payloads carry ids, versions and weights only: never memory content, embeddings,
emails or display names.
"""
# ruff: noqa: S608

from __future__ import annotations

import os
import re

from sqlalchemy import text

from alembic import context, op

revision = "20261029_0015"
down_revision = "20261016_0014"
branch_labels = None
depends_on = None

RUNTIME_ROLE_ENV = "SOURCEMIND_RUNTIME_ROLE"
RELAY_ROLE_ENV = "SOURCEMIND_GRAPH_RELAY_ROLE"
TABLE = "graph_outbox"
SEQUENCE = "graph_outbox_seq_seq"

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
            seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            event_id uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE,
            workspace_id uuid NOT NULL,
            event_type text NOT NULL,
            aggregate_id uuid NOT NULL,
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            published_at timestamptz
        )
        """
    )
    # The relay scans only what is still unpublished, in seq order.
    op.execute(
        f"CREATE INDEX ix_graph_outbox_unpublished ON {TABLE} (seq) WHERE published_at IS NULL"
    )
    op.execute(f"CREATE INDEX ix_graph_outbox_workspace_seq ON {TABLE} (workspace_id, seq)")

    op.execute(f"REVOKE ALL ON TABLE {TABLE} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON SEQUENCE {SEQUENCE} FROM PUBLIC")
    owner = _table_owner()
    if runtime_role != owner:
        op.execute(f'REVOKE ALL ON TABLE {TABLE} FROM "{runtime_role}"')
        op.execute(f'GRANT INSERT ON TABLE {TABLE} TO "{runtime_role}"')
        op.execute(f'REVOKE ALL ON SEQUENCE {SEQUENCE} FROM "{runtime_role}"')
        op.execute(f'GRANT USAGE ON SEQUENCE {SEQUENCE} TO "{runtime_role}"')
    if relay_role is not None and relay_role != owner:
        op.execute(f'REVOKE ALL ON TABLE {TABLE} FROM "{relay_role}"')
        op.execute(f'GRANT SELECT ON TABLE {TABLE} TO "{relay_role}"')
        op.execute(f'GRANT UPDATE (published_at) ON TABLE {TABLE} TO "{relay_role}"')


def downgrade() -> None:
    if not context.is_offline_mode():
        pending = (
            op.get_bind()
            .execute(text(f"SELECT count(*) FROM {TABLE} WHERE published_at IS NULL"))
            .scalar()
        )
        if pending:
            raise RuntimeError(
                f"Cannot drop {TABLE}: {pending} events are not yet published, and dropping "
                "them would desynchronise the graph. Let the relay drain them (or rebuild "
                "the graph afterwards) before rolling back."
            )
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
