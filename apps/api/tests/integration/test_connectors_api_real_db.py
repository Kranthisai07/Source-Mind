"""Connectors API against real PostgreSQL under RLS (runtime role, NOBYPASSRLS).

Route functions are called directly with RLS-scoped sessions (the pattern of
test_source_provenance_real_db.py). GitHub, Celery and retirement/requeue internals are
faked; the guards, SQL, fence/generation bookkeeping and tenancy are real.
"""

from __future__ import annotations

import hashlib
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from sourcemind.api.v1 import connectors as api
from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.core.dependencies import AuthenticatedUser
from sourcemind.core.exceptions import (
    ConnectorNotFoundError,
    ForbiddenError,
    WorkspaceAccessDeniedError,
)
from sourcemind.models.connector import (
    ArtifactLink,
    ConnectorConfig,
    ConnectorSyncCursor,
    ConnectorSyncLog,
    ConnectorWorkItem,
)
from sourcemind.models.document import Document
from sourcemind.schemas.connector import ConnectorCreateRequest, SyncTriggerRequest
from sourcemind.services.connectors import connect_ticket
from tests.integration.test_security_foundation_real_db import (
    SecurityTenantData,
    _seed_security_tenants,
)

pytestmark = pytest.mark.asyncio


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, key):
        return 1 if self.data.pop(key, None) is not None else 0

    async def eval(self, *_a):
        return [1, 1000]


@pytest.fixture
def fake_redis():
    fake = FakeRedis()
    with patch.object(connect_ticket, "get_redis", return_value=fake):
        yield fake


async def _session(
    engine: AsyncEngine, user_id: uuid.UUID, workspace_id: uuid.UUID | None
) -> AsyncSession:
    session = AsyncSession(engine, expire_on_commit=False)
    await set_rls_user_context(session, user_id)
    if workspace_id is not None:
        await set_rls_workspace_context(session, workspace_id)
    return session


def _user(uid: uuid.UUID) -> AuthenticatedUser:
    return AuthenticatedUser(uid, f"clerk-{uid}", f"{uid}@example.com")


def _hash() -> str:
    return hashlib.sha256(uuid.uuid4().hex.encode()).hexdigest()


class World:
    def __init__(self, data: SecurityTenantData) -> None:
        self.data = data
        self.connector_id = uuid.uuid4()
        self.docs: dict[str, uuid.UUID] = {}


@pytest.fixture
async def world(supabase_engine: AsyncEngine) -> World:
    data = await _seed_security_tenants(supabase_engine)
    w = World(data)
    ws = data.target_workspace_id
    async with await _session(supabase_engine, data.owner_id, ws) as s:
        s.add(
            ConnectorConfig(
                id=w.connector_id,
                workspace_id=ws,
                connector_type="github",
                display_name=f"conn-{w.connector_id.hex[:8]}",
                config={"repos": ["o/a"], "consent_workspace_readable": True},
                status="active",
                installation_id=77,
                authorized_github_user_id=4242,
                created_by=data.owner_id,
            )
        )
        await s.flush()

        def doc(label, status, *, gen=0, pipeline=None, memories=None, connector=True):
            d = Document(
                id=uuid.uuid4(),
                workspace_id=ws,
                submitter_id=data.owner_id,
                source_type="text",
                sha256_hash=_hash(),
                ingestion_status=status,
                connector_id=w.connector_id if connector else None,
                connector_generation=gen if connector else None,
                pipeline_data=pipeline or {},
                memory_count=memories,
            )
            s.add(d)
            w.docs[label] = d.id
            return d

        doc("failed_transient", "failed", pipeline={"error_code": "transient", "attempts": 3,
                                                    "next_retry_at": "2030-01-01T00:00:00Z"})
        doc("failed_budget", "failed", pipeline={"error_code": "budget", "attempts": 2})
        doc("failed_permanent", "failed", pipeline={"error_code": "permanent"})
        doc("failed_old_gen", "failed", gen=-1, pipeline={"error_code": "transient"})
        doc("failed_manual_other", "failed", connector=False, pipeline={"error_code": "transient"})
        doc("pending", "pending")
        doc("processing", "processing")
        doc("done", "completed", memories=3, pipeline={"publication": "published"})
        doc("done_nofacts", "completed", memories=0, pipeline={"publication": "no_facts"})
        await s.flush()
        s.add(ConnectorSyncCursor(connector_id=w.connector_id, workspace_id=ws, repo="o/a",
                                  artifact_type="issues", status="authorization_unverified"))
        s.add(ConnectorWorkItem(connector_id=w.connector_id, workspace_id=ws, repo="o/a",
                                artifact_type="issues", source_id="o/a#1", state="quarantined"))
        s.add(ConnectorSyncLog(connector_id=w.connector_id, sync_type="incremental",
                               status="running", trigger="manual", artifacts_found=5,
                               artifacts_filtered={"skip_bots": 2}))
        s.add(ConnectorSyncLog(connector_id=w.connector_id, sync_type="incremental",
                               status="failed", error_message="provider said Bearer abc",
                               error_code="rate_limited", artifacts_filtered={"skip_bots": 1}))
        s.add(ArtifactLink(workspace_id=ws, document_id=w.docs["done"], source_tool="github",
                           source_type="issue", source_id="o/a#9", connector_id=w.connector_id))
        await s.commit()
    # failed_old_gen got gen -1 above only to differ from current generation 0
    return w


# ── tenancy and roles ────────────────────────────────────────────────────────

async def test_other_workspace_gets_404_everywhere(supabase_engine, world, fake_redis):
    outsider = _user(world.data.other_workspace_member_id)
    cid = world.connector_id
    with patch.object(api, "enforce_rate_limit", AsyncMock()):
        for call in (
            lambda db: api.get_connector(cid, db, outsider),
            lambda db: api.connector_status(cid, db, outsider),
            lambda db: api.list_sync_logs(cid, db, outsider),
            lambda db: api.trigger_sync(cid, SyncTriggerRequest(), db, outsider),
            lambda db: api.delete_connector(cid, db, outsider),
            lambda db: api.retry_failed(cid, db, outsider),
            lambda db: api.rescan_connector(cid, db, outsider),
            lambda db: api.update_connector(
                cid, api.ConnectorUpdateRequest(status="paused"), db, outsider),
        ):
            async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
                await set_rls_user_context(db, outsider.user_id)
                with pytest.raises(ConnectorNotFoundError):
                    await call(db)
    # a missing connector answers identically
    async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
        await set_rls_user_context(db, outsider.user_id)
        with pytest.raises(ConnectorNotFoundError):
            await api.get_connector(uuid.uuid4(), db, outsider)


async def test_viewer_and_member_denied_admin_allowed(supabase_engine, world, fake_redis):
    cid = world.connector_id
    for uid in (world.data.viewer_id, world.data.member_id):
        async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
            await set_rls_user_context(db, uid)
            with pytest.raises(WorkspaceAccessDeniedError):
                await api.connector_status(cid, db, _user(uid))
        async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
            await set_rls_user_context(db, uid)
            with pytest.raises(WorkspaceAccessDeniedError):
                await api.delete_connector(cid, db, _user(uid))
        async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
            await set_rls_user_context(db, uid)
            with pytest.raises(WorkspaceAccessDeniedError):
                await api.github_authorize(world.data.target_workspace_id, db, _user(uid))
    async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
        await set_rls_user_context(db, world.data.admin_id)
        out = await api.connector_status(cid, db, _user(world.data.admin_id))
        assert out.connector_id == cid


# ── status ───────────────────────────────────────────────────────────────────

async def test_status_counts_scopes_and_logs(supabase_engine, world, fake_redis):
    admin = _user(world.data.admin_id)
    async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
        await set_rls_user_context(db, admin.user_id)
        s = await api.connector_status(world.connector_id, db, admin)
    d = s.documents
    # failed: transient, budget, permanent, old-gen = 4 (the non-connector doc is excluded)
    assert (d.pending, d.processing, d.completed, d.failed) == (1, 1, 2, 4)
    assert d.zero_facts == 1
    assert d.quarantined == 1
    assert s.state == "syncing"
    assert s.current_run is not None and s.current_run.artifacts_found == 5
    assert [(x.repo, x.artifact_type, x.status) for x in s.scopes] == [
        ("o/a", "issues", "authorization_unverified")
    ]
    assert s.filtered == {"skip_bots": 3}
    assert len(s.recent_logs) == 2
    failed = next(x for x in s.recent_logs if x.status == "failed")
    assert failed.error_code == "rate_limited"
    assert failed.model_dump()["error_message"] == "Connector sync failed."


# ── disconnect ───────────────────────────────────────────────────────────────

async def _row(engine, world, extra="") -> dict:
    async with await _session(engine, world.data.owner_id, world.data.target_workspace_id) as s:
        r = (await s.execute(
            text("SELECT status, generation, fence, next_sync_at, sync_lease_owner, "
                 "sync_lease_expires_at, installation_id, authorized_github_user_id "
                 "FROM connector_configs WHERE id = :i"), {"i": world.connector_id}
        )).mappings().one()
        return dict(r)


async def test_disconnect_default_keeps_everything(supabase_engine, world, fake_redis):
    admin = _user(world.data.admin_id)
    async with await _session(supabase_engine, world.data.owner_id,
                              world.data.target_workspace_id) as s:
        await s.execute(text(
            "UPDATE connector_configs SET sync_lease_owner='w', "
            "sync_lease_expires_at=now()+interval '5 min', next_sync_at=now() WHERE id=:i"),
            {"i": world.connector_id})
        await s.commit()
    retire = AsyncMock()
    async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
        await set_rls_user_context(db, admin.user_id)
        with patch.object(api, "retire_document_memories", retire):
            out = await api.delete_connector(world.connector_id, db, admin, remove_memories=False)
    retire.assert_not_called()
    assert (out.status, out.generation, out.memories_retired) == ("disconnected", 1, 0)
    row = await _row(supabase_engine, world)
    assert row["status"] == "disconnected" and row["generation"] == 1 and row["fence"] == 1
    assert row["next_sync_at"] is None and row["sync_lease_owner"] is None
    assert row["sync_lease_expires_at"] is None
    assert row["installation_id"] is None and row["authorized_github_user_id"] is None
    async with await _session(supabase_engine, world.data.owner_id,
                              world.data.target_workspace_id) as s:
        assert (await s.execute(text(
            "SELECT count(*) FROM artifact_links WHERE connector_id=:i"),
            {"i": world.connector_id})).scalar_one() == 1
        assert (await s.execute(text(
            "SELECT count(*) FROM connector_sync_logs WHERE connector_id=:i"),
            {"i": world.connector_id})).scalar_one() == 2
        assert (await s.execute(text(
            "SELECT count(*) FROM documents WHERE connector_id=:i"),
            {"i": world.connector_id})).scalar_one() == 8
    # sync / retry / patch are refused afterwards (409), through the same guard
    async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
        await set_rls_user_context(db, admin.user_id)
        with patch.object(api, "enforce_rate_limit", AsyncMock()), pytest.raises(Exception) as e:
            await api.trigger_sync(world.connector_id, SyncTriggerRequest(), db, admin)
        assert getattr(e.value, "http_status", None) == 409


async def test_disconnect_remove_memories_calls_retire_with_reason_and_scope(
    supabase_engine, world, fake_redis
):
    admin = _user(world.data.admin_id)
    calls = []

    async def fake_retire(session, *, workspace_id, document_id, reason, now=None):
        calls.append((workspace_id, document_id, reason))
        return SimpleNamespace(retired_ids=[uuid.uuid4()], flagged_ids=[])

    async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
        await set_rls_user_context(db, admin.user_id)
        with patch.object(api, "retire_document_memories", fake_retire):
            out = await api.delete_connector(world.connector_id, db, admin, remove_memories=True)
    expected = {v for k, v in world.docs.items() if k != "failed_manual_other"}
    assert {c[1] for c in calls} == expected
    assert {c[0] for c in calls} == {world.data.target_workspace_id}
    assert {c[2] for c in calls} == {"connector_removed"}
    assert out.documents_considered == len(expected) == out.memories_retired


async def test_pause_bumps_fence_and_clears_schedule(supabase_engine, world, fake_redis):
    admin = _user(world.data.admin_id)
    async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
        await set_rls_user_context(db, admin.user_id)
        out = await api.update_connector(
            world.connector_id, api.ConnectorUpdateRequest(status="paused"), db, admin)
    assert out.status == "paused"
    row = await _row(supabase_engine, world)
    assert row["fence"] == 1 and row["generation"] == 0 and row["next_sync_at"] is None


# ── retry-failed ─────────────────────────────────────────────────────────────

async def test_retry_failed_rearms_only_current_generation_retryable(
    supabase_engine, world, fake_redis
):
    admin = _user(world.data.admin_id)
    requeued = []

    async def fake_requeue(session, document_id, workspace_id, *, now):
        requeued.append((document_id, workspace_id))
        return document_id != world.docs["failed_budget"]  # receiver refuses this one

    async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
        await set_rls_user_context(db, admin.user_id)
        with patch.object(api.receiver, "requeue_transient_failure", fake_requeue, create=True):
            out = await api.retry_failed(world.connector_id, db, admin)
    assert {d for d, _ in requeued} == {world.docs["failed_transient"], world.docs["failed_budget"]}
    assert {w for _, w in requeued} == {world.data.target_workspace_id}
    assert (out.eligible, out.requeued, out.refused, out.remaining) == (2, 1, 1, 0)
    async with await _session(supabase_engine, world.data.owner_id,
                              world.data.target_workspace_id) as s:
        attempts = {
            r.id: r.a for r in (await s.execute(text(
                "SELECT id, (metadata->>'attempts')::int AS a, metadata ? 'next_retry_at' AS n "
                "FROM documents WHERE id = ANY(:ids)"),
                {"ids": list(world.docs.values())})).all()
        }
    assert attempts[world.docs["failed_transient"]] == 0
    assert attempts[world.docs["failed_budget"]] == 0
    assert attempts[world.docs["failed_permanent"]] is None  # untouched
    assert attempts[world.docs["failed_old_gen"]] is None  # other generation untouched


async def test_retry_failed_unavailable_without_receiver_support(
    supabase_engine, world, fake_redis
):
    admin = _user(world.data.admin_id)
    async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
        await set_rls_user_context(db, admin.user_id)
        with patch.object(api, "receiver", SimpleNamespace()), pytest.raises(Exception) as e:
            await api.retry_failed(world.connector_id, db, admin)
    assert getattr(e.value, "http_status", None) == 503


# ── rescan ───────────────────────────────────────────────────────────────────

async def test_rescan_marks_all_scopes_and_schedules(supabase_engine, world, fake_redis):
    admin = _user(world.data.admin_id)
    async with await _session(supabase_engine, world.data.owner_id,
                              world.data.target_workspace_id) as s:
        await s.execute(text(
            "UPDATE connector_sync_cursors SET phase='incremental', listing_complete=true, "
            "resume_id='x' WHERE connector_id=:i"), {"i": world.connector_id})
        await s.commit()
    async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
        await set_rls_user_context(db, admin.user_id)
        out = await api.rescan_connector(world.connector_id, db, admin)
    assert out.scopes_marked == 6
    async with await _session(supabase_engine, world.data.owner_id,
                              world.data.target_workspace_id) as s:
        rows = (await s.execute(text(
            "SELECT phase, listing_complete, resume_id FROM connector_sync_cursors "
            "WHERE connector_id=:i"), {"i": world.connector_id})).all()
        nxt = (await s.execute(text(
            "SELECT next_sync_at FROM connector_configs WHERE id=:i"),
            {"i": world.connector_id})).scalar_one()
    assert len(rows) == 6 and all(r == ("backfill", False, None) for r in rows)
    assert nxt is not None


# ── overlapping connectors are rejected, consistently, and under concurrency ──────────────────

async def _ticket_for(world, admin, repos):
    return await connect_ticket.store_ticket(
        workspace_id=world.data.target_workspace_id, user_id=admin.user_id, github_user_id=555,
        installation_id=99, repositories=repos)


def _create_req(repos, name, tid):
    return ConnectorCreateRequest(
        connector_type="github", display_name=name,
        config={"repos": repos, "consent_workspace_readable": True}, ticket_id=tid)


async def _create(engine, world, admin, repos, name):
    tid = await _ticket_for(world, admin, repos)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await set_rls_user_context(db, admin.user_id)
        return await api.create_connector(
            world.data.target_workspace_id, _create_req(repos, name, tid), db, admin, "r")


async def test_creating_a_connector_for_a_repository_another_live_connector_has_is_rejected(
    supabase_engine, world, fake_redis
):
    admin = _user(world.data.admin_id)
    with patch.object(api, "enforce_rate_limit", AsyncMock()), pytest.raises(Exception) as e:
        await _create(supabase_engine, world, admin, ["O/A", "o/free"], "overlap-1")  # world has o/a
    assert getattr(e.value, "http_status", None) == 409
    assert e.value.details["reason"] == "repository_already_connected"
    assert e.value.details["repositories"] == ["o/a"]  # only the clashing repo is named
    async with await _session(supabase_engine, world.data.owner_id,
                              world.data.target_workspace_id) as s:
        n = (await s.execute(text(
            "SELECT count(*) FROM connector_configs WHERE display_name = 'overlap-1'"))).scalar_one()
    assert n == 0


async def test_a_reconnect_after_the_old_connector_was_disconnected_is_allowed(
    supabase_engine, world, fake_redis
):
    admin = _user(world.data.admin_id)
    async with await _session(supabase_engine, world.data.owner_id,
                              world.data.target_workspace_id) as s:
        await s.execute(text("UPDATE connector_configs SET status='disconnected', generation = "
                             "generation + 1 WHERE id=:i"), {"i": world.connector_id})
        await s.commit()
    with patch.object(api, "enforce_rate_limit", AsyncMock()):
        out = await _create(supabase_engine, world, admin, ["o/a"], "reconnected")
    assert out.config["repos"] == ["o/a"]


@pytest.mark.parametrize("held_status", ["paused", "paused_budget", "error"])
async def test_a_paused_or_errored_connector_still_owns_its_repositories(
    supabase_engine, world, fake_redis, held_status
):
    admin = _user(world.data.admin_id)
    async with await _session(supabase_engine, world.data.owner_id,
                              world.data.target_workspace_id) as s:
        await s.execute(text("UPDATE connector_configs SET status=:st WHERE id=:i"),
                        {"st": held_status, "i": world.connector_id})
        await s.commit()
    with patch.object(api, "enforce_rate_limit", AsyncMock()), pytest.raises(Exception) as e:
        await _create(supabase_engine, world, admin, ["o/a"], "overlap-held")
    assert e.value.details["reason"] == "repository_already_connected"


async def test_resume_is_refused_when_a_live_connector_has_taken_the_repository(
    supabase_engine, world, fake_redis
):
    """Legacy / racing data: B is paused with the same repo that A (active) holds. Resuming B must not
    produce two live connectors on one repo."""
    admin = _user(world.data.admin_id)
    ws = world.data.target_workspace_id
    b = uuid.uuid4()
    async with await _session(supabase_engine, world.data.owner_id, ws) as s:
        s.add(ConnectorConfig(
            id=b, workspace_id=ws, connector_type="github", display_name=f"legacy-{b.hex[:6]}",
            config={"repos": ["O/A"]}, status="paused", installation_id=77,
            authorized_github_user_id=4242, created_by=world.data.owner_id))
        await s.commit()
    from datetime import UTC, datetime

    from sourcemind.services.connectors import lifecycle
    async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
        await set_rls_user_context(db, admin.user_id)
        with pytest.raises(lifecycle.RepositoryConflictError) as e:
            await lifecycle.resume(db, b, now=datetime.now(UTC))
        assert e.value.repos == ["O/A"] or e.value.repos == ["o/a"]
        await db.rollback()
    # Through the route the same refusal is a 409.
    with patch.object(api, "enforce_rate_limit", AsyncMock()):
        async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
            await set_rls_user_context(db, admin.user_id)
            with pytest.raises(Exception) as e2:
                await api.update_connector(
                    b, api.ConnectorUpdateRequest(status="active"), db, admin)
    assert getattr(e2.value, "http_status", None) == 409
    async with await _session(supabase_engine, world.data.owner_id, ws) as s:
        st = (await s.execute(text("SELECT status FROM connector_configs WHERE id=:i"),
                              {"i": b})).scalar_one()
    assert st == "paused"


async def test_concurrent_creates_for_the_same_repository_exactly_one_wins(
    supabase_engine, world, fake_redis, monkeypatch
):
    import asyncio

    from sourcemind.services.connectors import lifecycle

    # Hold every creator between "the check passed" and "the row is inserted". Without the
    # workspace lock all four pass the check inside this window and all four insert; with it the
    # others wait for the winner's commit and then fail the check.
    real_check = lifecycle.assert_repositories_unclaimed

    async def slow_check(*a, **k):
        await real_check(*a, **k)
        await asyncio.sleep(0.4)

    monkeypatch.setattr(lifecycle, "assert_repositories_unclaimed", slow_check)
    admin = _user(world.data.admin_id)
    async with await _session(supabase_engine, world.data.owner_id,
                              world.data.target_workspace_id) as s:
        await s.execute(text("UPDATE connector_configs SET status='disconnected' WHERE id=:i"),
                        {"i": world.connector_id})
        await s.commit()
    with patch.object(api, "enforce_rate_limit", AsyncMock()):
        results = await asyncio.gather(
            *[_create(supabase_engine, world, admin, ["race/repo"], f"race-{i}") for i in range(4)],
            return_exceptions=True,
        )
    wins = [r for r in results if not isinstance(r, BaseException)]
    losses = [r for r in results if isinstance(r, BaseException)]
    assert len(wins) == 1, results
    assert all(getattr(x, "details", {}).get("reason") == "repository_already_connected"
               for x in losses), losses
    async with await _session(supabase_engine, world.data.owner_id,
                              world.data.target_workspace_id) as s:
        n = (await s.execute(text(
            "SELECT count(*) FROM connector_configs WHERE config->'repos' ? 'race/repo' "
            "AND status <> 'disconnected'"))).scalar_one()
    assert n == 1


# ── re-arm quarantined items ─────────────────────────────────────────────────

async def _quarantine(engine, world, n: int = 2) -> None:
    async with await _session(engine, world.data.owner_id, world.data.target_workspace_id) as s:
        for i in range(n):
            await s.execute(text(
                "INSERT INTO connector_work_items (connector_id, workspace_id, repo, artifact_type,"
                " source_id, attempts, state, last_error_code) VALUES (:c, :w, 'o/a', 'issues',"
                " :sid, 3, 'quarantined', 'handoff_failed')"),
                {"c": world.connector_id, "w": world.data.target_workspace_id, "sid": f"o/a/i/{i}"})
        await s.commit()


async def test_rearm_is_admin_only_and_tenant_scoped(supabase_engine, world, fake_redis):
    await _quarantine(supabase_engine, world)
    member = _user(world.data.member_id)  # a plain member: may read, may not administer
    outsider = _user(world.data.other_workspace_member_id)
    with patch.object(api, "enforce_rate_limit", AsyncMock()):
        async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
            await set_rls_user_context(db, member.user_id)
            with pytest.raises(Exception) as denied:
                await api.rearm_quarantined(world.connector_id, db, member)
            assert getattr(denied.value, "http_status", None) in (403, 404)
        async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
            await set_rls_user_context(db, outsider.user_id)
            with pytest.raises(ConnectorNotFoundError):
                await api.rearm_quarantined(world.connector_id, db, outsider)
    async with await _session(supabase_engine, world.data.owner_id,
                              world.data.target_workspace_id) as s:
        states = {r[0] for r in (await s.execute(text(
            "SELECT state FROM connector_work_items WHERE connector_id=:i"),
            {"i": world.connector_id})).all()}
    assert states == {"quarantined"}  # neither refused call changed anything


async def test_rearm_resets_items_and_scopes_durably(supabase_engine, world, fake_redis):
    await _quarantine(supabase_engine, world)
    async with await _session(supabase_engine, world.data.owner_id,
                              world.data.target_workspace_id) as s:
        expected = (await s.execute(text(
            "SELECT count(*) FROM connector_work_items WHERE connector_id=:i AND "
            "state='quarantined'"), {"i": world.connector_id})).scalar_one()
    assert expected >= 2  # the fixture may already hold some
    admin = _user(world.data.admin_id)
    async with await _session(supabase_engine, world.data.owner_id,
                              world.data.target_workspace_id) as s:
        await s.execute(text(
            "UPDATE connector_sync_cursors SET phase='incremental', listing_complete=true, "
            "resume_id='x', consumed=9 WHERE connector_id=:i"), {"i": world.connector_id})
        await s.commit()
    with patch.object(api, "enforce_rate_limit", AsyncMock()):
        async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
            await set_rls_user_context(db, admin.user_id)
            out = await api.rearm_quarantined(world.connector_id, db, admin)
        async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
            await set_rls_user_context(db, admin.user_id)
            again = await api.rearm_quarantined(world.connector_id, db, admin)
    assert out.rearmed == expected and out.scopes_marked >= 1
    assert again.rearmed == 0  # idempotent: nothing left to re-arm
    async with await _session(supabase_engine, world.data.owner_id,
                              world.data.target_workspace_id) as s:
        items = (await s.execute(text(
            "SELECT state, attempts, last_error_code FROM connector_work_items "
            "WHERE connector_id=:i"), {"i": world.connector_id})).all()
        cursor = (await s.execute(text(
            "SELECT phase, listing_complete, resume_id, consumed FROM connector_sync_cursors "
            "WHERE connector_id=:i AND repo='o/a' AND artifact_type='issues'"),
            {"i": world.connector_id})).one()
        nxt = (await s.execute(text("SELECT next_sync_at FROM connector_configs WHERE id=:i"),
                               {"i": world.connector_id})).scalar_one()
    assert all(r == ("pending", 0, None) for r in items)
    assert tuple(cursor) == ("backfill", False, None, 0)  # the affected scope restarted
    assert nxt is not None  # a run is scheduled in the database itself


# ── create with a ticket, real insert ────────────────────────────────────────

async def test_create_stores_server_set_binding_and_rejects_unticketed_repo(
    supabase_engine, world, fake_redis
):
    admin = _user(world.data.admin_id)
    ws = world.data.target_workspace_id
    tid = await connect_ticket.store_ticket(
        workspace_id=ws, user_id=admin.user_id, github_user_id=555, installation_id=99,
        repositories=["acme/Widgets"],
    )

    def req(repos, name):
        return ConnectorCreateRequest(
            connector_type="github", display_name=name,
            config={"repos": repos, "consent_workspace_readable": True}, ticket_id=tid)

    async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
        await set_rls_user_context(db, admin.user_id)
        with pytest.raises(ForbiddenError):
            bad = req(["acme/widgets", "acme/secret"], "x1")
            await api.create_connector(ws, bad, db, admin, "r")
    async with await _session(supabase_engine, world.data.owner_id, ws) as s:
        assert (await s.execute(text(
            "SELECT count(*) FROM connector_configs WHERE display_name='x1'"))).scalar_one() == 0
    async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
        await set_rls_user_context(db, admin.user_id)
        out = await api.create_connector(ws, req(["acme/widgets"], "x2"), db, admin, "r")
    assert (out.installation_id, out.authorized_github_user_id) == (99, 555)
    assert out.created_by == admin.user_id and out.config["repos"] == ["acme/Widgets"]
    # ticket is single-use: a second create with it fails and stores nothing
    async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
        await set_rls_user_context(db, admin.user_id)
        with pytest.raises(ForbiddenError):
            await api.create_connector(ws, req(["acme/widgets"], "x3"), db, admin, "r")
    # a ticket minted for another workspace cannot be used in this one
    other_tid = await connect_ticket.store_ticket(
        workspace_id=world.data.other_workspace_id, user_id=admin.user_id, github_user_id=1,
        installation_id=2, repositories=["acme/widgets"])
    body = req(["acme/widgets"], "x4")
    body.ticket_id = other_tid
    async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
        await set_rls_user_context(db, admin.user_id)
        with pytest.raises(ForbiddenError):
            await api.create_connector(ws, body, db, admin, "r")

