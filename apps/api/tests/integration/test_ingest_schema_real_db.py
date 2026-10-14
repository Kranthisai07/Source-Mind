"""Automatic-ingestion schema (migration 0013) against real PostgreSQL.

Runs as the restricted runtime role (NOSUPERUSER, NOBYPASSRLS) under FORCE RLS.

Proves:
  * the runtime ACL on each new table is exactly SELECT/INSERT/UPDATE/DELETE and
    PUBLIC has nothing;
  * the new tables are workspace-scoped: members of another workspace and users
    with no membership see nothing and cannot write;
  * due connectors are listable across workspaces only through the SECURITY DEFINER
    function (ids only); the schedule table is closed to the runtime role and no
    connector_configs policy is loosened;
  * the status / trigger / phase / state CHECK constraints accept the new values
    and reject unknown ones;
  * pending-anchor uniqueness is per revision (a new revision can be handed off
    while an older anchor is un-backfilled) and still blocks a duplicate of the
    same revision;
  * ``artifact_heads`` refuses published_seq > latest_seq.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.models.connector import (
    ArtifactHead,
    ArtifactLink,
    ConnectorConfig,
    ConnectorSyncCursor,
    ConnectorWorkItem,
)
from sourcemind.models.document import Document
from tests.integration.test_github_author_links_real_db import (  # noqa: F401 - fixtures
    owner_database_url,
    owner_engine,
)
from tests.integration.test_security_foundation_real_db import (
    SecurityTenantData,
    _seed_security_tenants,
)

pytestmark = pytest.mark.asyncio

NEW_TABLES = ("connector_sync_cursors", "connector_work_items", "artifact_heads")
EXPECTED = {"SELECT", "INSERT", "UPDATE", "DELETE"}


async def _session(
    engine: AsyncEngine, user_id: uuid.UUID | None, workspace_id: uuid.UUID | None
) -> AsyncSession:
    session = AsyncSession(engine, expire_on_commit=False)
    if user_id is not None:
        await set_rls_user_context(session, user_id)
    if workspace_id is not None:
        await set_rls_workspace_context(session, workspace_id)
    return session


async def _connector(
    engine: AsyncEngine, data: SecurityTenantData, workspace_id: uuid.UUID, name: str
) -> uuid.UUID:
    async with await _session(engine, data.owner_id, workspace_id) as db:
        cfg = ConnectorConfig(
            workspace_id=workspace_id,
            connector_type="github",
            display_name=f"{name}-{uuid.uuid4().hex[:8]}",
            config={"repos": ["o/r"]},
            created_by=data.owner_id,
        )
        db.add(cfg)
        await db.commit()
        return cfg.id


async def test_runtime_grants_are_exact_and_public_has_none(supabase_engine: AsyncEngine) -> None:
    async with AsyncSession(supabase_engine) as db:
        for table in NEW_TABLES:
            rows = (
                await db.execute(
                    text(
                        "SELECT grantee, privilege_type FROM information_schema.role_table_grants "
                        "WHERE table_schema = 'public' AND table_name = :t "
                        "AND grantee IN ('PUBLIC', current_user)"
                    ),
                    {"t": table},
                )
            ).all()
            by_grantee: dict[str, set[str]] = {}
            for grantee, privilege in rows:
                by_grantee.setdefault(grantee, set()).add(privilege)
            assert "PUBLIC" not in by_grantee, f"{table} is readable by PUBLIC"
            assert by_grantee.get("sourcemind_test") == EXPECTED, (table, by_grantee)


async def test_new_tables_are_workspace_scoped_under_force_rls(
    supabase_engine: AsyncEngine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    connector_id = await _connector(supabase_engine, data, data.target_workspace_id, "scoped")

    # Rows are written by an administrator (writes are owner/admin only; see test_ingest_authz).
    async with await _session(supabase_engine, data.admin_id, data.target_workspace_id) as db:
        db.add(
            ConnectorSyncCursor(
                connector_id=connector_id, workspace_id=data.target_workspace_id,
                repo="o/r", artifact_type="issues",
            )
        )
        db.add(
            ConnectorWorkItem(
                connector_id=connector_id, workspace_id=data.target_workspace_id,
                repo="o/r", artifact_type="issues", source_id="o/r/issues/1",
            )
        )
        db.add(
            ArtifactHead(
                workspace_id=data.target_workspace_id, source_tool="github",
                source_type="issue", source_id="o/r/issues/1", latest_seq=1,
            )
        )
        await db.commit()

    # A member of the target workspace sees all three rows.
    async with await _session(supabase_engine, data.member_id, data.target_workspace_id) as db:
        for table in NEW_TABLES:
            n = (await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar()
            assert n >= 1, table

    # A member of ANOTHER workspace, and a user with no membership, see nothing.
    for uid in (data.other_workspace_member_id, data.zero_membership_id):
        async with await _session(supabase_engine, uid, None) as db:
            for table in NEW_TABLES:
                n = (
                    await db.execute(
                        text(f"SELECT count(*) FROM {table} WHERE workspace_id = :w"),
                        {"w": data.target_workspace_id},
                    )
                ).scalar()
                assert n == 0, (table, uid)

    # Writing into a workspace the user does not belong to is rejected by RLS.
    async with await _session(supabase_engine, data.other_workspace_member_id, None) as db:
        db.add(
            ArtifactHead(
                workspace_id=data.target_workspace_id, source_tool="github",
                source_type="issue", source_id="o/r/issues/99",
            )
        )
        with pytest.raises(DBAPIError):
            await db.commit()


async def test_due_connector_listing_is_cross_tenant_but_ids_only(
    supabase_engine: AsyncEngine,
) -> None:
    """The tick lists due connectors across workspaces through one SECURITY DEFINER
    function; the schedule table itself is closed to the runtime role, and no RLS
    policy on connector_configs is loosened."""
    data = await _seed_security_tenants(supabase_engine)
    due_a = await _connector(supabase_engine, data, data.target_workspace_id, "due-a")
    due_b = await _connector(supabase_engine, data, data.other_workspace_id, "due-b")
    paused = await _connector(supabase_engine, data, data.target_workspace_id, "paused")
    leased = await _connector(supabase_engine, data, data.target_workspace_id, "leased")
    future = await _connector(supabase_engine, data, data.target_workspace_id, "future")
    async with await _session(supabase_engine, data.owner_id, data.target_workspace_id) as db:
        await db.execute(
            text("UPDATE connector_configs SET status = 'paused' WHERE id = :i"), {"i": paused}
        )
        await db.execute(
            text(
                "UPDATE connector_configs SET sync_lease_expires_at = now() + interval '5 min' "
                "WHERE id = :i"
            ),
            {"i": leased},
        )
        await db.execute(
            text(
                "UPDATE connector_configs SET next_sync_at = now() + interval '1 hour' "
                "WHERE id = :i"
            ),
            {"i": future},
        )
        await db.commit()

    # No user context at all: the function still lists both workspaces' due ids...
    async with AsyncSession(supabase_engine) as db:
        rows = (await db.execute(text("SELECT * FROM sm_list_due_connectors(500)"))).all()
        listed = {r.connector_id for r in rows}
        assert due_a in listed and due_b in listed
        assert not ({paused, leased, future} & listed)
        # ids and the creator only; no connector content, config or tokens
        assert set(rows[0]._fields) == {
            "connector_id", "workspace_id", "created_by", "creator_active", "acting_admin",
        }
        by_id = {r.connector_id: r for r in rows}
        assert by_id[due_a].created_by == data.owner_id and by_id[due_a].creator_active is True

        # ...but nothing else opens up: the schedule table is closed to the runtime
        # role and connector_configs itself is still invisible without membership.
        with pytest.raises(DBAPIError, match="permission denied"):
            await db.execute(text("SELECT count(*) FROM sm_connector_schedule"))
        await db.rollback()
        ids = {r[0] for r in (await db.execute(text("SELECT id FROM connector_configs"))).all()}
        assert due_a not in ids and due_b not in ids

    # The schedule follows connector changes (pause removes, resume re-adds, delete drops).
    async with await _session(supabase_engine, data.owner_id, data.target_workspace_id) as db:
        await db.execute(
            text("UPDATE connector_configs SET status = 'paused' WHERE id = :i"), {"i": due_a}
        )
        await db.commit()
    async with AsyncSession(supabase_engine) as db:
        listed = {r.connector_id for r in (await db.execute(
            text("SELECT * FROM sm_list_due_connectors(500)"))).all()}
        assert due_a not in listed and due_b in listed


async def test_check_constraints_accept_new_values_and_reject_unknown(
    supabase_engine: AsyncEngine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    connector_id = await _connector(supabase_engine, data, data.target_workspace_id, "checks")

    async with await _session(supabase_engine, data.owner_id, data.target_workspace_id) as db:
        for status in ("disconnected", "paused_budget", "active"):
            await db.execute(
                text("UPDATE connector_configs SET status = :s WHERE id = :i"),
                {"s": status, "i": connector_id},
            )
        await db.commit()

    bad = (
        ("UPDATE connector_configs SET status = 'bogus' WHERE id = :i", {"i": connector_id}),
        (
            "UPDATE connector_configs SET sync_interval_minutes = 1 WHERE id = :i",
            {"i": connector_id},
        ),
        (
            "INSERT INTO connector_sync_logs (connector_id, sync_type, status, trigger) "
            "VALUES (:i, 'full', 'running', 'carrier-pigeon')",
            {"i": connector_id},
        ),
        (
            "INSERT INTO connector_sync_cursors (connector_id, workspace_id, repo, "
            "artifact_type, status) VALUES (:i, :w, 'o/r', 'issues', 'wrong')",
            {"i": connector_id, "w": data.target_workspace_id},
        ),
        (
            "INSERT INTO connector_work_items (connector_id, workspace_id, repo, "
            "artifact_type, source_id, state) VALUES (:i, :w, 'o/r', 'issues', 'x', 'lost')",
            {"i": connector_id, "w": data.target_workspace_id},
        ),
    )
    for sql, params in bad:
        async with await _session(supabase_engine, data.owner_id, data.target_workspace_id) as db:
            with pytest.raises(IntegrityError):
                await db.execute(text(sql), params)

    async with await _session(supabase_engine, data.owner_id, data.target_workspace_id) as db:
        for trigger in ("manual", "scheduled", "recovery"):
            await db.execute(
                text(
                    "INSERT INTO connector_sync_logs (connector_id, sync_type, status, trigger) "
                    "VALUES (:i, 'incremental', 'continuing', :t)"
                ),
                {"i": connector_id, "t": trigger},
            )
        await db.commit()


async def test_pending_anchor_uniqueness_is_per_revision(supabase_engine: AsyncEngine) -> None:
    data = await _seed_security_tenants(supabase_engine)
    async with await _session(supabase_engine, data.owner_id, data.target_workspace_id) as db:
        docs = []
        for _ in range(3):
            d = Document(
                workspace_id=data.target_workspace_id, submitter_id=data.owner_id,
                source_type="text", sha256_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                ingestion_status="pending",
            )
            db.add(d)
            await db.flush()
            docs.append(d.id)

        def anchor(doc: uuid.UUID, seq: int) -> ArtifactLink:
            return ArtifactLink(
                workspace_id=data.target_workspace_id, document_id=doc, memory_id=None,
                source_tool="github", source_type="issue", source_id="o/r/issues/7",
                revision_seq=seq,
            )

        db.add_all([anchor(docs[0], 1), anchor(docs[1], 2)])  # two revisions: allowed
        await db.flush()
        db.add(anchor(docs[2], 2))  # duplicate pending anchor of revision 2: refused
        with pytest.raises(IntegrityError):
            await db.flush()


async def test_artifact_head_refuses_published_ahead_of_latest(
    supabase_engine: AsyncEngine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    async with await _session(supabase_engine, data.owner_id, data.target_workspace_id) as db:
        db.add(
            ArtifactHead(
                workspace_id=data.target_workspace_id, source_tool="github",
                source_type="issue", source_id="o/r/issues/8", latest_seq=1, published_seq=2,
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()


async def test_inactive_creator_is_reported_with_an_active_admin_to_act_as(
    supabase_engine: AsyncEngine,
) -> None:
    """When a connector's creator has left, a scheduled run cannot read the connector under
    RLS as the creator. The due list reports it and names an active owner/admin whose normal
    context can record the outcome; no privileged write exists."""
    data = await _seed_security_tenants(supabase_engine)
    gone = await _connector(supabase_engine, data, data.target_workspace_id, "gone-owner")
    kept = await _connector(supabase_engine, data, data.target_workspace_id, "kept-owner")
    async with await _session(supabase_engine, data.owner_id, data.target_workspace_id) as db:
        await db.execute(
            text("UPDATE connector_configs SET created_by = :u WHERE id = :i"),
            {"u": data.member_id, "i": gone},
        )
        await db.commit()

    async with AsyncSession(supabase_engine) as db:
        rows = {
            r.connector_id: r
            for r in (await db.execute(text("SELECT * FROM sm_list_due_connectors(500)"))).all()
        }
    assert rows[kept].creator_active is True
    # created_by is a plain member: not an admin, so not an active creator-admin either.
    assert rows[gone].creator_active is False
    admins = {data.owner_id, data.admin_id, data.backup_owner_id}
    assert rows[gone].acting_admin in admins  # an owner or admin, never the member

    # The acting admin's ordinary RLS context can record the outcome.
    async with await _session(supabase_engine, rows[gone].acting_admin, None) as db:
        result = await db.execute(
            text(
                "UPDATE connector_configs SET status='error', last_error_code='owner_inactive' "
                "WHERE id = :i"
            ),
            {"i": gone},
        )
        assert result.rowcount == 1
        await db.commit()
    # ...and the member (the departed creator's stand-in) cannot, since the row is hidden.
    async with await _session(supabase_engine, data.zero_membership_id, None) as db:
        result = await db.execute(
            text("UPDATE connector_configs SET status='active' WHERE id = :i"), {"i": gone}
        )
        assert result.rowcount == 0


async def test_connector_nobody_can_act_for_is_not_listed_as_due(
    supabase_engine: AsyncEngine, owner_engine: AsyncEngine
) -> None:
    """A connector in a soft-deleted workspace can never be rescheduled by the tick (it cannot
    write under RLS), so listing it would keep it at the front of the 500-row window forever and
    starve healthy connectors. The due list leaves it out."""
    data = await _seed_security_tenants(supabase_engine)
    healthy = await _connector(supabase_engine, data, data.target_workspace_id, "healthy")
    doomed = await _connector(supabase_engine, data, data.other_workspace_id, "doomed")
    # Soft-delete the workspace the way the sync trigger records it (internal table, owner only).
    async with owner_engine.begin() as conn:
        result = await conn.execute(
            text("UPDATE sm_workspace_bootstraps SET deleted_at = now() WHERE workspace_id = :w"),
            {"w": data.other_workspace_id},
        )
        assert result.rowcount == 1

    async with AsyncSession(supabase_engine) as db:
        due = {
            r.connector_id for r in (await db.execute(text("SELECT * FROM sm_list_due_connectors(500)"))).all()
        }
    assert healthy in due
    assert doomed not in due
