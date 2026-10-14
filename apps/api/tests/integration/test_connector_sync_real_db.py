"""Fenced sync orchestrator against real PostgreSQL (runtime role, FORCE RLS).

SIMULATED: GitHub (a fake discovery + a recording client), the repository-access
verifier, retirement, and (except in one test) the receiver. REAL: PostgreSQL with the
migrated schema, the restricted runtime role, RLS, the lease/fence SQL, cursors, work
items, artifact heads, sync logs and the SECURITY DEFINER tick function.

Test ids follow the plan: T-L1..L5, T-C1..C4, T-R1..R3, T-X1..X6, T-U3/U4/U5/U9, T-DC3.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from sourcemind.connectors.github.connector import GitHubConnector
from sourcemind.connectors.github.contracts import (
    AccessVerdict,
    Discovered,
    GoneError,
    NotFoundError,
    ScopeResult,
    ScopeState,
    VerifierResult,
)
from sourcemind.connectors.github.mapper import ConnectorDocument
from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.models.connector import ArtifactHead, ConnectorConfig, ConnectorSyncLog
from sourcemind.models.document import Document
from sourcemind.services.connectors import sync_state as ss
from sourcemind.services.connectors.sync_state import LeaseLostError
from tests.integration.test_security_acceptance_real_services import _configure_environment
from tests.integration.test_security_foundation_real_db import (
    SecurityTenantData,
    _seed_security_tenants,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

CONNECTOR = "sourcemind.connectors.github.connector"
REPO = "acme/repo"
BASE_TS = datetime(2026, 10, 1, tzinfo=UTC)


# ── fakes ────────────────────────────────────────────────────────────────────


class Crash(BaseException):
    """Simulates a killed worker: not an Exception, so nothing handles it."""


@dataclass
class Entry:
    ts: datetime
    id: str
    doc: ConnectorDocument


def _doc(repo: str, n: int, stype: str = "issue", seg: str = "issues") -> ConnectorDocument:
    sid = f"{repo}/{seg}/{n}"
    return ConnectorDocument(
        source_tool="github",
        source_type=stype,
        source_id=sid,
        title=f"item {n}",
        content=f"body of {sid} {uuid.uuid4().hex}",
        idempotency_key=ConnectorDocument.make_key("github", stype, sid),
        metadata={},
    )


def _entries(repo: str, count: int, start: int = 1, stype: str = "issue", seg: str = "issues"):
    out = []
    for n in range(start, start + count):
        d = _doc(repo, n, stype, seg)
        out.append(Entry(BASE_TS + timedelta(minutes=n), d.source_id, d))
    return out


class FakeDiscovery:
    """Stands in for ``discover_scope``: honours resume key, window and max_items."""

    def __init__(self) -> None:
        self.catalog: dict[tuple[str, str], list[Entry]] = {}
        self.calls: list[tuple[str, str]] = []
        self.hooks: dict[tuple[str, str], Any] = {}
        self.errors: dict[tuple[str, str], tuple[int, Exception]] = {}
        self.inventory_complete = True
        self.ignore_state = False
        self.window_high: datetime | None = None

    def __call__(self, client, *, owner, repo, scope, state, now, limits):
        self.calls.append((f"{owner}/{repo}", scope))
        return _FakeRun(self, f"{owner}/{repo}", scope, state, limits)


class _FakeRun:
    def __init__(self, d, repo, scope, state, limits) -> None:
        self.d, self.repo, self.scope, self.state, self.limits = d, repo, scope, state, limits
        self.result: ScopeResult | None = None

    async def items(self):
        d, state = self.d, self.state
        entries = list(d.catalog.get((self.repo, self.scope), []))
        high = state.window_high or d.window_high
        if high is not None:
            entries = [e for e in entries if e.ts <= high]
        start = 0
        if state.resume_id and not d.ignore_state:
            ids = [e.id for e in entries]
            start = ids.index(state.resume_id) + 1 if state.resume_id in ids else 0
        err = d.errors.get((self.repo, self.scope))
        emitted = 0
        last: Entry | None = None
        if err and err[0] == 0:
            raise err[1]
        for i in range(start, len(entries)):
            if emitted >= self.limits.max_items:
                break
            entry = entries[i]
            hook = d.hooks.get((self.scope, entry.id))
            if hook is not None:
                await hook()
            yield Discovered(document=entry.doc, position=(entry.ts, entry.id))
            emitted += 1
            last = entry
            if err and err[0] == emitted:
                raise err[1]
        exhausted = start + emitted >= len(entries)
        self.result = ScopeResult(
            state=ScopeState(
                phase="incremental" if exhausted else "backfill",
                window_high=high,
                resume_ts=last.ts if last else state.resume_ts,
                resume_id=last.id if last else state.resume_id,
                listing_complete=exhausted,
            ),
            inventory_complete=d.inventory_complete and exhausted,
            full_inventory=d.inventory_complete and exhausted,
            seen_source_ids=frozenset(e.id for e in d.catalog.get((self.repo, self.scope), [])),
        )


class FakeReceiver:
    """Thin fake of ``receive``: honours ``pre_commit``, commits, deduplicates."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.created: list[str] = []
        self.seen: set[tuple[str, str]] = set()
        self.fail_ids: set[str] = set()

    async def __call__(self, **kw):
        link = kw["artifact_link"]
        sid = link["source_id"]
        self.calls.append(sid)
        if sid in self.fail_ids:
            raise RuntimeError("poison artifact")
        key = (sid, link["source_revision"])
        if key in self.seen:
            return {"already_exists": True, "reason": "same_revision", "revision_seq": 1}
        if kw.get("pre_commit") is not None:
            await kw["pre_commit"](kw["session"])
        await kw["session"].commit()
        self.seen.add(key)
        self.created.append(sid)
        return {"already_exists": False, "revision_seq": 1, "document_id": str(uuid.uuid4())}


class RecordingClient:
    """Records every GitHub call except the installation listing (metadata)."""

    def __init__(self, installed: set[str]) -> None:
        self.installed = installed
        self.content_calls: list[str] = []

    async def list_installation_repositories(self):
        return list(self.installed)

    def __getattr__(self, name: str):
        def _call(*_a, **_k):
            self.content_calls.append(name)
            raise AssertionError(f"content call {name}")

        return _call


class FakeVerifier:
    def __init__(self, verdict: AccessVerdict = AccessVerdict.AUTHORIZED) -> None:
        self.verdict = verdict
        self.calls: list[dict[str, Any]] = []

    async def verify(self, *, owner: str, repo: str, github_user_id: int) -> VerifierResult:
        self.calls.append({"owner": owner, "repo": repo, "github_user_id": github_user_id})
        return VerifierResult(self.verdict, self.verdict.value)


def _settings(**over: Any) -> SimpleNamespace:
    base = {
        "connector_lease_seconds": 600,
        "connector_max_repos": 5,
        "connector_max_handoffs_per_run": 300,
        "connector_max_handoff_attempts": 3,
        "connector_import_days": 90,
        "connector_max_items_per_type": 500,
        "connector_max_segments_per_pr": 30,
        "connector_review_reconcile_batch": 25,
        "connector_max_artifact_chars": 40_000,
        "connector_skip_bots": True,
        "connector_skip_empty_body": True,
    }
    base.update(over)
    return SimpleNamespace(**base)


# ── database helpers ─────────────────────────────────────────────────────────


async def _open(engine: AsyncEngine, user_id: uuid.UUID, workspace_id: uuid.UUID) -> AsyncSession:
    session = AsyncSession(engine, expire_on_commit=False)
    await set_rls_user_context(session, user_id)
    await set_rls_workspace_context(session, workspace_id)
    return session


async def _make_connector(
    engine: AsyncEngine,
    data: SecurityTenantData,
    *,
    repos: list[str] | None = None,
    creator: uuid.UUID | None = None,
    status: str = "active",
    next_sync_at: datetime | None = None,
    lease_expires_at: datetime | None = None,
) -> uuid.UUID:
    cid = uuid.uuid4()
    session = await _open(engine, data.owner_id, data.target_workspace_id)
    async with session:
        session.add(
            ConnectorConfig(
                id=cid,
                workspace_id=data.target_workspace_id,
                connector_type="github",
                display_name=f"sync-{cid.hex[:10]}",
                config={"repos": [REPO] if repos is None else repos},
                status=status,
                installation_id=4242,
                authorized_github_user_id=777,
                created_by=creator or data.admin_id,
                next_sync_at=next_sync_at,
                sync_lease_expires_at=lease_expires_at,
            )
        )
        await session.commit()
    return cid


async def _rows(engine, data, sql: str, **params) -> list[Any]:
    session = await _open(engine, data.owner_id, data.target_workspace_id)
    async with session:
        return list((await session.execute(text(sql), params)).fetchall())


async def _one(engine, data, sql: str, **params) -> Any:
    rows = await _rows(engine, data, sql, **params)
    return rows[0][0] if rows else None


async def _cursor(engine, data, cid, scope="issues", repo=REPO):
    rows = await _rows(
        engine,
        data,
        "SELECT resume_id, status, listing_complete, last_error_code, window_high "
        "FROM connector_sync_cursors WHERE connector_id = :c AND repo = :r "
        "AND artifact_type = :s",
        c=cid,
        r=repo,
        s=scope,
    )
    return rows[0] if rows else None


async def _exec(engine, data, sql: str, **params) -> None:
    session = await _open(engine, data.owner_id, data.target_workspace_id)
    async with session:
        await session.execute(text(sql), params)
        await session.commit()


async def _expire_lease(engine, data, cid) -> None:
    await _exec(
        engine,
        data,
        "UPDATE connector_configs SET sync_lease_expires_at = now() - interval '1 second' "
        "WHERE id = :c",
        c=cid,
    )


class Harness:
    def __init__(
        self,
        engine: AsyncEngine,
        data: SecurityTenantData,
        cid: uuid.UUID,
        *,
        discovery: FakeDiscovery | None = None,
        receiver: FakeReceiver | None = None,
        verifier: FakeVerifier | None | str = "default",
        installed: set[str] | None = None,
        settings: SimpleNamespace | None = None,
        retire: AsyncMock | None = None,
        creator: uuid.UUID | None = None,
        patch_receive: bool = True,
    ) -> None:
        self.engine, self.data, self.cid = engine, data, cid
        self.discovery = discovery or FakeDiscovery()
        self.receiver = receiver or FakeReceiver()
        self.verifier = FakeVerifier() if verifier == "default" else verifier
        self.client = RecordingClient({REPO} if installed is None else installed)
        self.settings = settings or _settings()
        self.retire = retire or AsyncMock()
        self.creator = creator or data.admin_id
        self.patch_receive = patch_receive

    async def run(self, trigger: str = "manual"):
        session = await _open(self.engine, self.creator, self.data.target_workspace_id)
        async with session:
            connector = GitHubConnector(
                config=SimpleNamespace(id=self.cid),
                auth=SimpleNamespace(_installation_id="4242"),
                session=session,
                workspace_id=self.data.target_workspace_id,
                user_id=self.creator,
                client=self.client,
                discover=self.discovery,
                verifier=self.verifier,
                retire=self.retire,
                settings=self.settings,
            )
            if self.patch_receive:
                with patch(f"{CONNECTOR}.receive", new=self.receiver):
                    return await connector.run(trigger=trigger)
            return await connector.run(trigger=trigger)


@pytest.fixture
async def world(supabase_engine: AsyncEngine):
    data = await _seed_security_tenants(supabase_engine)
    return supabase_engine, data


# ── T-L: lease and fence ─────────────────────────────────────────────────────


async def test_T_L1_stalled_worker_loses_the_lease_and_every_write_is_rejected(world) -> None:
    engine, data = world
    cid = await _make_connector(engine, data)
    h = Harness(engine, data, cid)
    ents = _entries(REPO, 3)
    h.discovery.catalog[(REPO, "issues")] = ents
    b_token: dict[str, Any] = {}

    async def stall_then_takeover() -> None:
        # worker A stalls past expiry; worker B acquires on an independent session
        await _expire_lease(engine, data, cid)
        s = await _open(engine, data.admin_id, data.target_workspace_id)
        async with s:
            b_token["t"] = await ss.acquire_lease(s, cid, lease_seconds=600)

    h.discovery.hooks[("issues", ents[1].id)] = stall_then_takeover

    outcome = await h.run()

    assert outcome.status == "lease_lost"
    assert b_token["t"] is not None and b_token["t"].fence == 2
    assert h.receiver.created == [ents[0].id]  # nothing created after the takeover
    cur = await _cursor(engine, data, cid)
    assert cur.resume_id == ents[0].id  # key unchanged by the superseded worker
    # the superseded worker cannot touch the log or the schedule either
    assert await _one(engine, data, "SELECT status FROM connector_sync_logs WHERE connector_id=:c", c=cid) == "running"
    assert await _one(engine, data, "SELECT next_sync_at FROM connector_configs WHERE id=:c", c=cid) is None
    # and a direct fenced write with A's old token raises
    s = await _open(engine, data.admin_id, data.target_workspace_id)
    async with s:
        with pytest.raises(LeaseLostError):
            await ss.renew_lease(s, ss.LeaseToken(cid, 1, "a"), lease_seconds=60)


async def test_T_L2_heartbeat_extends_the_lease(world) -> None:
    engine, data = world
    cid = await _make_connector(engine, data)
    s = await _open(engine, data.admin_id, data.target_workspace_id)
    async with s:
        token = await ss.acquire_lease(s, cid, lease_seconds=5)
        assert token is not None
        before = await _one(engine, data, "SELECT sync_lease_expires_at FROM connector_configs WHERE id=:c", c=cid)
        await ss.renew_lease(s, token, lease_seconds=600)
        await s.commit()
    after = await _one(engine, data, "SELECT sync_lease_expires_at FROM connector_configs WHERE id=:c", c=cid)
    assert after - before > timedelta(minutes=5)


async def test_T_L3_reaper_marks_a_dead_workers_running_log_interrupted(world) -> None:
    engine, data = world
    cid = await _make_connector(engine, data, repos=[])
    s = await _open(engine, data.owner_id, data.target_workspace_id)
    async with s:
        s.add(ConnectorSyncLog(connector_id=cid, sync_type="incremental", status="running"))
        await s.commit()

    outcome = await Harness(engine, data, cid).run()

    assert outcome.status == "completed"
    statuses = sorted(
        r[0] for r in await _rows(engine, data, "SELECT status FROM connector_sync_logs WHERE connector_id=:c", c=cid)
    )
    assert statuses == ["completed", "interrupted"]
    # lease released and next sync scheduled on completion
    row = (await _rows(engine, data, "SELECT sync_lease_expires_at, next_sync_at FROM connector_configs WHERE id=:c", c=cid))[0]
    assert row[0] is None and row[1] is not None


async def test_T_L4_and_T_DC3_disconnect_bumps_the_fence_and_kills_the_active_run(world) -> None:
    engine, data = world
    cid = await _make_connector(engine, data)
    h = Harness(engine, data, cid)
    ents = _entries(REPO, 4)
    h.discovery.catalog[(REPO, "issues")] = ents

    async def disconnect_mid_fetch() -> None:
        await _exec(
            engine,
            data,
            "UPDATE connector_configs SET status='disconnected', fence = fence + 1, "
            "next_sync_at = NULL, generation = generation + 1 WHERE id = :c",
            c=cid,
        )

    h.discovery.hooks[("issues", ents[2].id)] = disconnect_mid_fetch

    outcome = await h.run()

    assert outcome.status == "lease_lost"
    assert h.receiver.created == [ents[0].id, ents[1].id]
    assert (await _cursor(engine, data, cid)).resume_id == ents[1].id
    # a disconnected connector cannot be re-leased
    s = await _open(engine, data.admin_id, data.target_workspace_id)
    async with s:
        assert await ss.acquire_lease(s, cid, lease_seconds=60) is None


async def test_T_L5_two_simultaneous_triggers_one_runs_one_skips(world) -> None:
    engine, data = world
    cid = await _make_connector(engine, data)
    s1 = await _open(engine, data.admin_id, data.target_workspace_id)
    s2 = await _open(engine, data.admin_id, data.target_workspace_id)
    async with s1, s2:
        results = await asyncio.gather(
            ss.acquire_lease(s1, cid, lease_seconds=600),
            ss.acquire_lease(s2, cid, lease_seconds=600),
        )
    assert sorted(r is None for r in results) == [False, True]

    h = Harness(engine, data, cid)
    outcome = await h.run()  # lease is held by the winner
    assert outcome.status == "skipped_locked"
    assert h.discovery.calls == [] and h.client.content_calls == []


# ── T-C: continuation ────────────────────────────────────────────────────────


async def test_T_C1_n2_over_7_items_covers_all_with_one_handoff_each(world) -> None:
    engine, data = world
    cid = await _make_connector(engine, data)
    h = Harness(engine, data, cid, settings=_settings(connector_max_handoffs_per_run=2))
    ents = _entries(REPO, 7)
    h.discovery.catalog[(REPO, "issues")] = ents

    statuses = []
    for _ in range(8):
        out = await h.run()
        statuses.append(out.status)
        if out.status == "completed":
            break
        assert out.status == "continuing"
    assert statuses[-1] == "completed" and len(statuses) == 4
    assert h.receiver.created == [e.id for e in ents]
    assert all(h.receiver.calls.count(e.id) == 1 for e in ents)
    # continuing runs are rescheduled immediately
    assert (await _cursor(engine, data, cid)).listing_complete is True
    # a further run finds nothing new and calls the receiver for nothing
    again = await h.run()
    assert again.status == "completed" and len(h.receiver.calls) == 7


async def test_T_C2_small_scope_completes_in_the_first_run_beside_a_large_one(world) -> None:
    engine, data = world
    cid = await _make_connector(engine, data)
    h = Harness(engine, data, cid, settings=_settings(connector_max_handoffs_per_run=12))
    h.discovery.catalog[(REPO, "issues")] = _entries(REPO, 40)
    h.discovery.catalog[(REPO, "commits")] = _entries(REPO, 3, stype="commit", seg="commits")

    out = await h.run()

    assert out.status == "continuing"
    assert (await _cursor(engine, data, cid, "commits")).listing_complete is True
    assert (await _cursor(engine, data, cid, "issues")).listing_complete is False
    assert len(h.receiver.created) == 12


async def test_T_C3_crash_after_handoff_resumes_after_the_key_and_replay_is_deduplicated(world) -> None:
    engine, data = world
    cid = await _make_connector(engine, data)
    h = Harness(engine, data, cid)
    ents = _entries(REPO, 5)
    h.discovery.catalog[(REPO, "issues")] = ents

    async def die() -> None:
        raise Crash

    h.discovery.hooks[("issues", ents[2].id)] = die
    with pytest.raises(Crash):
        await h.run()
    assert h.receiver.created == [ents[0].id, ents[1].id]
    assert (await _cursor(engine, data, cid)).resume_id == ents[1].id

    await _expire_lease(engine, data, cid)  # the dead worker's lease runs out
    del h.discovery.hooks[("issues", ents[2].id)]
    out = await h.run()
    assert out.status == "completed"
    assert h.receiver.created == [e.id for e in ents]  # resumed after the key
    assert all(h.receiver.calls.count(e.id) == 1 for e in ents)
    logs = sorted(r[0] for r in await _rows(engine, data, "SELECT status FROM connector_sync_logs WHERE connector_id=:c", c=cid))
    assert logs == ["completed", "interrupted"]

    # a replay of everything (discovery lost its position) is deduplicated by the receiver
    h.discovery.ignore_state = True
    replay = await h.run()
    assert replay.counts["skipped"] == 5 and replay.counts["new"] == 0
    assert len(h.receiver.created) == 5
    assert (await _cursor(engine, data, cid)).resume_id == ents[-1].id


async def test_T_C4_items_arriving_during_backfill_do_not_extend_the_window(world) -> None:
    engine, data = world
    cid = await _make_connector(engine, data)
    h = Harness(engine, data, cid, settings=_settings(connector_max_handoffs_per_run=2))
    ents = _entries(REPO, 4)
    high = ents[-1].ts + timedelta(seconds=1)
    h.discovery.window_high = high
    h.discovery.catalog[(REPO, "issues")] = list(ents)

    first = await h.run()
    assert first.status == "continuing"
    late = _entries(REPO, 1, start=99)[0]
    late.ts = high + timedelta(hours=1)  # arrives after the window was fixed
    h.discovery.catalog[(REPO, "issues")].append(late)
    for _ in range(6):
        if (await h.run()).status == "completed":
            break

    assert h.receiver.created == [e.id for e in ents]  # the late item is not in this window
    assert (await _cursor(engine, data, cid)).window_high == high


# ── T-R: recovery and quarantine ─────────────────────────────────────────────


async def _stranded_doc(engine, data, cid, *, status, pipeline) -> uuid.UUID:
    s = await _open(engine, data.owner_id, data.target_workspace_id)
    async with s:
        doc = Document(
            workspace_id=data.target_workspace_id,
            submitter_id=data.admin_id,
            source_type="text",
            sha256_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            ingestion_status=status,
            pipeline_data=pipeline,
            connector_id=cid,
        )
        s.add(doc)
        await s.commit()
        return doc.id


async def test_T_R1_and_R2_recovery_sweep_requeues_without_github_and_exhausted_stays_failed(world) -> None:
    engine, data = world
    cid = await _make_connector(engine, data, repos=[])
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    due = await _stranded_doc(engine, data, cid, status="failed", pipeline={"error_code": "transient", "attempts": 1, "next_retry_at": past})
    incomplete = await _stranded_doc(engine, data, cid, status="failed", pipeline={"error_code": "incomplete_extraction", "attempts": 0})
    not_yet = await _stranded_doc(engine, data, cid, status="failed", pipeline={"error_code": "transient", "attempts": 1, "next_retry_at": future})
    exhausted = await _stranded_doc(engine, data, cid, status="failed", pipeline={"error_code": "transient", "attempts": 3, "next_retry_at": past})
    permanent = await _stranded_doc(engine, data, cid, status="failed", pipeline={"error_code": "permanent", "attempts": 0})
    orphaned = await _stranded_doc(engine, data, cid, status="pending", pipeline={"dispatch_state": "uncertain", "current_stage": "queued"})
    queued = await _stranded_doc(engine, data, cid, status="pending", pipeline={"dispatch_state": "queued"})
    # the discovery position must not move because of downstream failures
    s = await _open(engine, data.admin_id, data.target_workspace_id)
    async with s:
        await ss.upsert_cursor(s, workspace_id=data.target_workspace_id, connector_id=cid, repo=REPO, scope="issues", resume_id="pos-7")
        await s.commit()

    h = Harness(engine, data, cid)
    requeue = AsyncMock(return_value=True)
    recover = AsyncMock()
    with (
        patch(f"{CONNECTOR}.requeue_transient_failure", new=requeue),
        patch(f"{CONNECTOR}.recover_pending_dispatch", new=recover),
    ):
        out = await h.run(trigger="scheduled")

    assert out.status == "completed"
    assert {c.args[1] for c in requeue.await_args_list} == {due, incomplete}
    assert [c.args[1] for c in recover.await_args_list] == [orphaned]
    untouched = {not_yet, exhausted, permanent, queued}
    touched = {c.args[1] for c in requeue.await_args_list} | {c.args[1] for c in recover.await_args_list}
    assert not (untouched & touched)
    assert out.sweep["requeued"] == 2 and out.sweep["redispatched"] == 1
    assert h.discovery.calls == [] and h.client.content_calls == []  # no GitHub at all
    # R2: exhausted attempts stay failed and visible; cursor untouched
    statuses = dict(await _rows(engine, data, "SELECT id, ingestion_status FROM documents WHERE connector_id=:c", c=cid))
    assert statuses[exhausted] == "failed" and statuses[permanent] == "failed"
    assert (await _cursor(engine, data, cid)).resume_id == "pos-7"


async def test_T_R3_poison_artifact_is_quarantined_after_three_attempts_and_later_items_proceed(world) -> None:
    engine, data = world
    cid = await _make_connector(engine, data)
    h = Harness(engine, data, cid)
    ents = _entries(REPO, 3)
    h.discovery.catalog[(REPO, "issues")] = ents
    h.receiver.fail_ids = {ents[1].id}

    for attempt in (1, 2):
        out = await h.run()
        assert out.status == "failed" and out.error_code == "handoff_failed"
        assert (await _cursor(engine, data, cid)).resume_id == ents[0].id  # never past the failure
        state = (await _rows(engine, data, "SELECT attempts, state FROM connector_work_items WHERE connector_id=:c", c=cid))[0]
        assert (state.attempts, state.state) == (attempt, "pending")
    assert h.receiver.created == [ents[0].id]

    third = await h.run()  # attempts reach the limit: quarantined, the key passes it
    assert third.counts["quarantined"] == 1 and third.counts["failed"] == 1
    state = (await _rows(engine, data, "SELECT attempts, state FROM connector_work_items WHERE connector_id=:c", c=cid))[0]
    assert (state.attempts, state.state) == (3, "quarantined")
    assert h.receiver.created == [ents[0].id, ents[2].id]  # the later item proceeded
    assert (await _cursor(engine, data, cid)).resume_id == ents[2].id
    log = (await _rows(engine, data, "SELECT artifacts_failed FROM connector_sync_logs WHERE connector_id=:c ORDER BY started_at DESC LIMIT 1", c=cid))[0]
    assert log.artifacts_failed == 1

    # re-arm (admin): the item is attempted again from a fresh count
    await _exec(engine, data, "UPDATE connector_work_items SET state='pending', attempts=0 WHERE connector_id=:c", c=cid)
    h.receiver.fail_ids = set()
    h.discovery.ignore_state = True
    rearmed = await h.run()
    assert rearmed.status == "completed"
    assert ents[1].id in h.receiver.created


# ── T-X: deletion rule ───────────────────────────────────────────────────────


async def _head(engine, data, source_id: str, *, missing: int = 0) -> uuid.UUID:
    s = await _open(engine, data.owner_id, data.target_workspace_id)
    async with s:
        doc = Document(
            workspace_id=data.target_workspace_id,
            submitter_id=data.admin_id,
            source_type="text",
            sha256_hash=hashlib.sha256(source_id.encode() + uuid.uuid4().bytes).hexdigest(),
            ingestion_status="completed",
        )
        s.add(doc)
        await s.flush()
        s.add(
            ArtifactHead(
                workspace_id=data.target_workspace_id,
                source_tool="github",
                source_type="issue",
                source_id=source_id,
                latest_seq=1,
                published_seq=1,
                published_document_id=doc.id,
                missing_scans=missing,
            )
        )
        await s.commit()
        return doc.id


async def _missing(engine, data, source_id: str) -> int:
    return await _one(engine, data, "SELECT missing_scans FROM artifact_heads WHERE source_id=:s", s=source_id)


async def _deletion_world(world, *, with_gone: bool = False):
    engine, data = world
    cid = await _make_connector(engine, data)
    h = Harness(engine, data, cid)
    kept = _entries(REPO, 1)  # issue 1 is still listed
    h.discovery.catalog[(REPO, "issues")] = kept
    sid_present, sid_gone = f"{REPO}/issues/1", f"{REPO}/issues/2"
    await _head(engine, data, sid_present, missing=1)
    gone_doc = await _head(engine, data, sid_gone)
    return engine, data, cid, h, sid_present, sid_gone, gone_doc


async def test_T_X1_hidden_404_retires_nothing(world) -> None:
    engine, data, cid, h, present, gone, _doc_id = await _deletion_world(world)
    h.discovery.errors[(REPO, "issues")] = (0, NotFoundError("404"))

    out = await h.run()

    assert out.status == "failed" and out.error_code == "not_found"
    assert (await _cursor(engine, data, cid)).status == "error"
    assert await _missing(engine, data, gone) == 0 and await _missing(engine, data, present) == 1
    h.retire.assert_not_awaited()


async def test_T_X2_capped_or_incomplete_inventory_retires_nothing(world) -> None:
    engine, data, cid, h, present, gone, _doc_id = await _deletion_world(world)
    h.discovery.inventory_complete = False

    for _ in range(3):
        await h.run()

    assert await _missing(engine, data, gone) == 0
    h.retire.assert_not_awaited()


async def test_T_X3_one_complete_scan_only_marks(world) -> None:
    engine, data, cid, h, present, gone, _doc_id = await _deletion_world(world)

    await h.run()

    assert await _missing(engine, data, gone) == 1
    assert await _missing(engine, data, present) == 0  # reset: it is still listed
    h.retire.assert_not_awaited()


async def test_T_X4_second_consecutive_complete_scan_retires(world) -> None:
    engine, data, cid, h, present, gone, gone_doc = await _deletion_world(world)

    await h.run()
    h.retire.assert_not_awaited()
    await h.run()

    assert await _missing(engine, data, gone) == 2
    h.retire.assert_awaited_once()
    kwargs = h.retire.await_args.kwargs
    assert kwargs["document_id"] == gone_doc and kwargs["reason"] == "source_deleted"
    assert kwargs["workspace_id"] == data.target_workspace_id


async def test_T_X4b_an_incomplete_scan_between_two_complete_ones_does_not_count(world) -> None:
    engine, data, cid, h, present, gone, _doc_id = await _deletion_world(world)

    await h.run()  # complete: 1
    h.discovery.inventory_complete = False
    await h.run()  # incomplete: unchanged
    assert await _missing(engine, data, gone) == 1
    h.retire.assert_not_awaited()


async def test_T_X5_installation_losing_the_repo_is_access_lost_and_retires_nothing(world) -> None:
    engine, data, cid, h, present, gone, _doc_id = await _deletion_world(world)
    h.client.installed = set()

    out = await h.run()

    assert out.status == "failed" and out.error_code == "access_lost"
    assert (await _cursor(engine, data, cid)).status == "access_lost"
    assert h.discovery.calls == []
    assert await _missing(engine, data, gone) == 0
    h.retire.assert_not_awaited()


async def test_T_X6_410_gone_retires_immediately(world) -> None:
    engine, data, cid, h, present, gone, gone_doc = await _deletion_world(world)
    exc = GoneError("410")
    exc.source_id = gone  # type: ignore[attr-defined]
    h.discovery.errors[(REPO, "issues")] = (0, exc)

    await h.run()

    h.retire.assert_awaited_once()
    assert h.retire.await_args.kwargs["document_id"] == gone_doc
    assert h.retire.await_args.kwargs["reason"] == "source_deleted"


# ── T-U: repository authorization (C11) ──────────────────────────────────────


async def _assert_no_content(h: Harness) -> None:
    assert h.discovery.calls == []
    assert h.client.content_calls == []
    assert h.receiver.calls == []


async def test_T_U3_denied_verdict_pauses_the_scope_as_reauthorization_required(world) -> None:
    engine, data = world
    cid = await _make_connector(engine, data)
    h = Harness(engine, data, cid, verifier=FakeVerifier(AccessVerdict.DENIED))
    h.discovery.catalog[(REPO, "issues")] = _entries(REPO, 2)

    out = await h.run()

    assert out.status == "failed" and out.error_code == "reauthorization_required"
    assert (await _cursor(engine, data, cid)).status == "reauthorization_required"
    await _assert_no_content(h)
    assert h.verifier.calls[0]["github_user_id"] == 777


@pytest.mark.parametrize("verifier", [FakeVerifier(AccessVerdict.UNVERIFIED), None, "raises"])
async def test_T_U4_unverified_or_missing_verification_makes_zero_content_calls(world, verifier) -> None:
    engine, data = world
    cid = await _make_connector(engine, data)
    if verifier == "raises":
        verifier = SimpleNamespace(verify=AsyncMock(side_effect=RuntimeError("503")))
    h = Harness(engine, data, cid, verifier=verifier)
    h.discovery.catalog[(REPO, "issues")] = _entries(REPO, 2)

    out = await h.run()

    assert out.status == "failed" and out.error_code == "authorization_unverified"
    assert (await _cursor(engine, data, cid)).status == "authorization_unverified"
    await _assert_no_content(h)


async def test_T_U5_no_grace_window_after_a_successful_past_check(world) -> None:
    engine, data = world
    cid = await _make_connector(engine, data)
    verifier = FakeVerifier(AccessVerdict.AUTHORIZED)
    h = Harness(engine, data, cid, verifier=verifier)
    h.discovery.catalog[(REPO, "issues")] = _entries(REPO, 2)
    assert (await h.run()).status == "completed"
    assert len(h.receiver.created) == 2
    imported = len(h.discovery.calls)

    verifier.verdict = AccessVerdict.UNVERIFIED  # the historical authorization is not enough
    h.discovery.catalog[(REPO, "issues")] = _entries(REPO, 4)
    out = await h.run()

    assert out.error_code == "authorization_unverified"
    assert len(h.discovery.calls) == imported  # no new batch was fetched
    assert len(h.receiver.created) == 2
    assert len(verifier.calls) >= 7  # checked before every batch of every scope, every run


async def test_T_U9_denial_and_unavailability_have_different_codes_and_neither_imports(world) -> None:
    engine, data = world
    codes = {}
    for verdict in (AccessVerdict.DENIED, AccessVerdict.UNVERIFIED):
        cid = await _make_connector(engine, data)
        h = Harness(engine, data, cid, verifier=FakeVerifier(verdict))
        h.discovery.catalog[(REPO, "issues")] = _entries(REPO, 1)
        codes[verdict] = (await h.run()).error_code
        await _assert_no_content(h)
    assert codes[AccessVerdict.DENIED] != codes[AccessVerdict.UNVERIFIED]


# ── authorization of the run itself, tick, credit ────────────────────────────


async def test_run_for_a_creator_who_is_no_longer_an_admin_is_owner_inactive(world) -> None:
    engine, data = world
    cid = await _make_connector(engine, data)
    await _exec(
        engine,
        data,
        "UPDATE workspace_members SET role='member' WHERE workspace_id=:w AND user_id=:u",
        w=data.target_workspace_id,
        u=data.admin_id,
    )
    h = Harness(engine, data, cid)
    h.discovery.catalog[(REPO, "issues")] = _entries(REPO, 2)

    out = await h.run()

    assert out.status == "failed" and out.error_code == "owner_inactive"
    assert await _one(engine, data, "SELECT status FROM connector_configs WHERE id=:c", c=cid) == "error"
    await _assert_no_content(h)


async def test_other_installation_token_is_refused(world) -> None:
    engine, data = world
    cid = await _make_connector(engine, data)
    h = Harness(engine, data, cid)
    session = await _open(engine, data.admin_id, data.target_workspace_id)
    async with session:
        connector = GitHubConnector(
            config=SimpleNamespace(id=cid),
            auth=SimpleNamespace(_installation_id="9999"),  # another tenant's installation
            session=session,
            workspace_id=data.target_workspace_id,
            user_id=data.admin_id,
            client=h.client,
            discover=h.discovery,
            verifier=h.verifier,
            settings=h.settings,
        )
        out = await connector.run()
    assert out.error_code == "installation_mismatch"
    await _assert_no_content(h)


async def test_tick_selection_excludes_paused_leased_and_future_connectors(world) -> None:
    engine, data = world
    now = datetime.now(UTC)
    due = await _make_connector(engine, data, next_sync_at=now - timedelta(minutes=1))
    never_run = await _make_connector(engine, data, next_sync_at=None)
    paused = await _make_connector(engine, data, status="paused", next_sync_at=now - timedelta(minutes=1))
    leased = await _make_connector(engine, data, next_sync_at=now - timedelta(minutes=1), lease_expires_at=now + timedelta(minutes=5))
    future = await _make_connector(engine, data, next_sync_at=now + timedelta(hours=1))

    async with AsyncSession(engine) as no_user:  # the tick has no user context
        rows = (await no_user.execute(text("SELECT connector_id FROM sm_list_due_connectors(500)"))).fetchall()
    listed = {r[0] for r in rows}

    assert {due, never_run} <= listed
    assert not ({paused, leased, future} & listed)


# ── the real receiver (SIMULATED only: GitHub, broker) ───────────────────────


async def test_real_receiver_handoff_submitter_is_technical_and_creator_is_not_credited(
    world, monkeypatch, supabase_url, security_test_redis_url
) -> None:
    from sourcemind.core.config import get_settings
    from sourcemind.core.redis_client import close_redis, init_redis
    from sourcemind.workers import ingestion as worker

    engine, data = world
    cid = await _make_connector(engine, data)
    monkeypatch.setattr(
        worker.process_document, "apply_async", lambda **kw: SimpleNamespace(id=kw["task_id"])
    )
    _configure_environment(monkeypatch, supabase_url, security_test_redis_url)
    get_settings.cache_clear()
    await init_redis()
    try:
        ent = _entries(REPO, 1)[0]
        ent.doc.metadata = {"author": {"github_user_id": 4_100_001, "login": "alice", "kind": "github_account"}}
        h = Harness(engine, data, cid, patch_receive=False)
        h.discovery.catalog[(REPO, "issues")] = [ent]

        first = await h.run()
        assert first.status == "completed" and first.counts["new"] == 1

        docs = await _rows(
            engine,
            data,
            "SELECT d.id, d.submitter_id, d.metadata FROM documents d "
            "JOIN artifact_links a ON a.document_id = d.id WHERE a.source_id = :s",
            s=ent.id,
        )
        assert len(docs) == 1
        assert docs[0].submitter_id == data.admin_id  # technical submitter only
        attribution = docs[0].metadata["attribution"]
        assert attribution["link_user_id"] is None  # unlinked author stays unresolved
        assert str(data.admin_id) not in json.dumps(attribution)
        resolved = await _one(engine, data, "SELECT resolved_user_id FROM artifact_links WHERE source_id=:s", s=ent.id)
        assert resolved is None
        assert (await _cursor(engine, data, cid)).resume_id == ent.id

        # discovery replays the same item: still exactly one document and one link
        h.discovery.ignore_state = True
        replay = await h.run()
        assert replay.counts["skipped"] == 1 and replay.counts["new"] == 0
        assert await _one(engine, data, "SELECT count(*) FROM artifact_links WHERE source_id=:s", s=ent.id) == 1
    finally:
        await close_redis()
        get_settings.cache_clear()
