"""Who may write the automatic-ingestion tables, and which roles may touch the cross-tenant objects.

Restricted runtime role (NOSUPERUSER NOBYPASSRLS, FORCE RLS), one workspace pair, one role per
persona. Findings this file pins (see decision log D-043):

* GAP 1 (viewer-role writes). The three new tables first used the same member-level rule as the tables
  they reference for reads AND writes, so at the database level a viewer could insert, rewrite or delete
  heads, cursors and work items (for example forging ``published_seq`` so a revision is never
  published). No HTTP route lets a viewer do that (every connector action needs ADMINISTER), so it was
  reachable only through a compromised or mistaken runtime path; the database now matches the
  application: members read, owners/admins write and lock.
* GAP 2 (cross-workspace objects). ``sm_connector_schedule`` is deliberately not under RLS (the
  scheduler lists due connectors across tenants) and ``sm_list_due_connectors`` is deliberately
  cross-tenant. The runtime role may call the function (ids only) and may not read the table; nobody
  else may do either, and migration 0013 sweeps grants held by any other role (platforms with default
  privileges grant new objects to roles of their own).
"""

# ruff: noqa: F811

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.models.connector import ConnectorConfig
from tests.integration.test_github_author_links_real_db import (  # noqa: F401 - fixtures
    owner_database_url,
    owner_engine,
)
from tests.integration.test_security_foundation_real_db import (
    SecurityTenantData,
    _seed_security_tenants,
)

pytestmark = pytest.mark.asyncio

TABLES = ("artifact_heads", "connector_sync_cursors", "connector_work_items")
MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions"
    / "20261015_0013_automatic_ingestion_schema.py"
)

_ROW = {
    "artifact_heads": (
        "INSERT INTO artifact_heads (workspace_id, source_tool, source_type, source_id, latest_seq)"
        " VALUES (:w, 'github', 'issue', :tag, 1)",
        "UPDATE artifact_heads SET published_seq = 1 WHERE workspace_id = :w AND source_id = :tag",
        "DELETE FROM artifact_heads WHERE workspace_id = :w AND source_id = :tag",
        "SELECT 1 FROM artifact_heads WHERE workspace_id = :w AND source_id = :tag",
        "SELECT 1 FROM artifact_heads WHERE workspace_id = :w AND source_id = :tag FOR UPDATE",
    ),
    "connector_sync_cursors": (
        "INSERT INTO connector_sync_cursors (connector_id, workspace_id, repo, artifact_type)"
        " VALUES (:c, :w, :tag, 'issues')",
        "UPDATE connector_sync_cursors SET phase = 'incremental' WHERE connector_id = :c"
        " AND repo = :tag",
        "DELETE FROM connector_sync_cursors WHERE connector_id = :c AND repo = :tag",
        "SELECT 1 FROM connector_sync_cursors WHERE connector_id = :c AND repo = :tag",
        "SELECT 1 FROM connector_sync_cursors WHERE connector_id = :c AND repo = :tag FOR UPDATE",
    ),
    "connector_work_items": (
        "INSERT INTO connector_work_items (connector_id, workspace_id, repo, artifact_type,"
        " source_id) VALUES (:c, :w, 'o/r', 'issues', :tag)",
        "UPDATE connector_work_items SET state = 'done' WHERE connector_id = :c AND source_id = :tag",
        "DELETE FROM connector_work_items WHERE connector_id = :c AND source_id = :tag",
        "SELECT 1 FROM connector_work_items WHERE connector_id = :c AND source_id = :tag",
        "SELECT 1 FROM connector_work_items WHERE connector_id = :c AND source_id = :tag FOR UPDATE",
    ),
}


async def _as(engine: AsyncEngine, user: uuid.UUID, ws: uuid.UUID | None) -> AsyncSession:
    s = AsyncSession(engine, expire_on_commit=False)
    await set_rls_user_context(s, user)
    if ws is not None:
        await set_rls_workspace_context(s, ws)
    return s


async def _connector(engine: AsyncEngine, data: SecurityTenantData, ws: uuid.UUID) -> uuid.UUID:
    async with await _as(engine, data.owner_id, ws) as s:
        cfg = ConnectorConfig(
            workspace_id=ws, connector_type="github", display_name=f"authz-{uuid.uuid4().hex[:8]}",
            config={"repos": ["o/r"]}, created_by=data.owner_id,
        )
        s.add(cfg)
        await s.commit()
        return cfg.id


async def _try(engine, user, ws, sql: str, **params) -> tuple[str, int]:
    """('ok', rowcount) or ('denied', 0): a row-level-security violation raises."""
    async with await _as(engine, user, ws) as s:
        try:
            res = await s.execute(text(sql), params)
            await s.commit()
            return "ok", res.rowcount if res.returns_rows is False else len(res.all())
        except DBAPIError as exc:
            await s.rollback()
            assert "row-level security" in str(exc.orig) or "permission denied" in str(exc.orig)
            return "denied", 0


@pytest.mark.parametrize("table", TABLES)
async def test_viewers_and_members_read_but_never_write_or_lock(
    supabase_engine: AsyncEngine, table: str
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    ws = data.target_workspace_id
    cid = await _connector(supabase_engine, data, ws)
    ins, upd, dele, sel, lock = _ROW[table]
    tag = f"authz-{uuid.uuid4().hex[:8]}"
    p = {"c": cid, "w": ws, "tag": tag}

    assert (await _try(supabase_engine, data.admin_id, ws, ins, **p))[0] == "ok"  # admin writes

    for who, user in (("viewer", data.viewer_id), ("member", data.member_id)):
        assert (await _try(supabase_engine, user, ws, sel, **p)) == ("ok", 1), who  # reads
        assert (await _try(supabase_engine, user, ws, ins, **{**p, "tag": tag + "x"}))[0] == "denied"
        # UPDATE / DELETE find no visible row to change: nothing happens and nothing is raised.
        assert await _try(supabase_engine, user, ws, upd, **p) == ("ok", 0), who
        assert await _try(supabase_engine, user, ws, dele, **p) == ("ok", 0), who
        assert await _try(supabase_engine, user, ws, lock, **p) == ("ok", 0), who  # no row lock

    # Nothing the viewer or member attempted changed the row; the admin still can.
    async with await _as(supabase_engine, data.owner_id, ws) as s:
        assert (await s.execute(text(sel), p)).all()
    assert await _try(supabase_engine, data.admin_id, ws, upd, **p) == ("ok", 1)
    assert await _try(supabase_engine, data.admin_id, ws, dele, **p) == ("ok", 1)


@pytest.mark.parametrize("table", TABLES)
async def test_an_admin_of_one_workspace_cannot_read_or_write_the_other(
    supabase_engine: AsyncEngine, table: str
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    other = data.other_workspace_id
    cid = await _connector(supabase_engine, data, other)
    ins, upd, dele, sel, lock = _ROW[table]
    tag = f"cross-{uuid.uuid4().hex[:8]}"
    p = {"c": cid, "w": other, "tag": tag}
    assert (await _try(supabase_engine, data.owner_id, other, ins, **p))[0] == "ok"

    admin = data.admin_id  # administers the TARGET workspace only
    for ctx in (data.target_workspace_id, None):  # with and without a workspace context
        assert await _try(supabase_engine, admin, ctx, sel, **p) == ("ok", 0)  # invisible
        assert await _try(supabase_engine, admin, ctx, upd, **p) == ("ok", 0)
        assert await _try(supabase_engine, admin, ctx, dele, **p) == ("ok", 0)
        assert await _try(supabase_engine, admin, ctx, lock, **p) == ("ok", 0)
        # a row naming the other workspace cannot be created either
        assert (await _try(supabase_engine, admin, ctx, ins, **{**p, "tag": tag + str(ctx)}))[0] \
            == "denied"
    async with await _as(supabase_engine, data.owner_id, other) as s:
        assert (await s.execute(text(sel), p)).all()  # untouched


async def test_cross_tenant_objects_are_reachable_only_as_designed(
    supabase_engine: AsyncEngine, owner_engine: AsyncEngine
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    await _connector(supabase_engine, data, data.target_workspace_id)
    await _connector(supabase_engine, data, data.other_workspace_id)

    async with AsyncSession(supabase_engine) as s:
        # The runtime role cannot read the unprotected schedule table...
        with pytest.raises(DBAPIError):
            await s.execute(text("SELECT * FROM sm_connector_schedule"))
        await s.rollback()
        # ...and CAN call the cross-tenant function, which returns ids and nothing else.
        res = await s.execute(text("SELECT * FROM sm_list_due_connectors(500)"))
        assert set(res.keys()) == {
            "connector_id", "workspace_id", "created_by", "creator_active", "acting_admin"
        }
        workspaces = {r.workspace_id for r in res.all()}
        assert {data.target_workspace_id, data.other_workspace_id} <= workspaces

    async with owner_engine.connect() as c:
        row = (await c.execute(text(
            "SELECT has_function_privilege('sourcemind_test', "
            "'sm_list_due_connectors(integer)', 'EXECUTE'), "
            "has_function_privilege('sourcemind_test', 'sm_sync_connector_schedule()', 'EXECUTE'), "
            "has_function_privilege('public', 'sm_list_due_connectors(integer)', 'EXECUTE'), "
            "has_table_privilege('public', 'sm_connector_schedule', 'SELECT'), "
            "has_table_privilege('sourcemind_test', 'sm_connector_schedule', 'SELECT')"
        ))).one()
    assert tuple(row) == (True, False, False, False, False)


async def test_the_migration_sweeps_grants_held_by_any_other_role(
    owner_engine: AsyncEngine,
) -> None:
    """Platforms with default privileges give new objects to roles of their own. The sweep revokes
    every grant not held by the owner or the named runtime role. Proven by naming a DIFFERENT role as
    "the runtime role": the real runtime role becomes an 'other grantee' and loses its grants.
    Rolled back, so the shared database is untouched."""
    spec = importlib.util.spec_from_file_location("mig_0013", MIGRATION)
    assert spec and spec.loader
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)

    async with owner_engine.connect() as conn:
        trans = await conn.begin()
        try:
            statements: list[str] = []
            mig.sweep_other_grantees(statements.append, "some_other_runtime_role")
            for sql in statements:
                await conn.execute(text(sql))
            for table in (*TABLES, "sm_connector_schedule"):
                has = (await conn.execute(text(
                    f"SELECT has_table_privilege('sourcemind_test', '{table}', "
                    "'SELECT,INSERT,UPDATE,DELETE')"
                ))).scalar_one()
                assert has is False, table
            fn = (await conn.execute(text(
                "SELECT has_function_privilege('sourcemind_test', "
                "'sm_list_due_connectors(integer)', 'EXECUTE')"
            ))).scalar_one()
            assert fn is False
        finally:
            await trans.rollback()
    # and the real runtime role keeps its access when it IS the named role
    async with owner_engine.connect() as conn:
        assert (await conn.execute(text(
            "SELECT has_table_privilege('sourcemind_test', 'artifact_heads', 'SELECT')"
        ))).scalar_one() is True
