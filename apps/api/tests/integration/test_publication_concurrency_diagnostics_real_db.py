"""Two revisions of one artifact finalizing together (bounded, instrumented).

An earlier two-revision finalize test hung. Diagnosis (PostgreSQL ``log_lock_waits``, backend
PIDs tagged with ``application_name``, ``pg_blocking_pids()``):

* It was NOT a wait cycle and NOT a test-barrier deadlock. The newer revision's pipeline
  blocked behind the older revision's open transaction.
* Cause: the pipeline claims its document row with ``SELECT ... FOR UPDATE SKIP LOCKED`` and
  holds it for the whole run (model calls included). The newer document references the older
  one (``supersedes_document_id``). PostgreSQL re-checks that foreign key when the newer
  document's own row is updated a second time in the same transaction, and the check takes
  ``FOR KEY SHARE`` on the older row, which only a full ``FOR UPDATE`` conflicts with. The log
  line was ``SELECT 1 FROM ONLY "public"."documents" x WHERE "id" = $1 FOR KEY SHARE OF x``.
* Fix: claim with ``FOR NO KEY UPDATE`` (still exclusive against a second claimer, but
  compatible with ``FOR KEY SHARE``).

Regression: both pipelines are parked just before the head lock with an explicit barrier. With
the old lock mode the second pipeline never gets there (it waits for the first transaction), so
the barrier times out and the sampled lock history is attached to the failure. Every wait is
bounded.
"""

# ruff: noqa: F811

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.test_ingestion_worker_contract import (  # noqa: F401 - fixtures
    ingestion_worker_database_url,
    ingestion_worker_engine,
    ingestion_worker_environment,
    ingestion_worker_redis_url,
    worker_harness,
)
from tests.integration.test_revision_publication_real_db import (  # noqa: F401 - fixtures
    _fact,
    _facts,
    _head,
    _install_retire,
    _live,
    _local_retire,
    _no_broker,
    _run,
    _two_revisions,
    world,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_SAMPLE_SQL = text(
    """
    SELECT a.pid, a.application_name AS app, a.state, a.wait_event_type, a.wait_event,
           pg_blocking_pids(a.pid) AS blocked_by, left(a.query, 90) AS query
    FROM pg_stat_activity a
    WHERE a.datname = current_database() AND a.pid <> pg_backend_pid()
      AND (a.state <> 'idle' OR a.wait_event_type = 'Lock')
    """
)


@dataclass
class Sampler:
    """Polls lock state from its own connection until stopped."""

    engine: AsyncEngine
    history: list[list[dict[str, Any]]] = field(default_factory=list)
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _task: asyncio.Task[None] | None = None

    async def _loop(self) -> None:
        async with self.engine.connect() as conn:
            while not self._stop.is_set():
                rows = (await conn.execute(_SAMPLE_SQL)).mappings().all()
                self.history.append([dict(r) for r in rows])
                await asyncio.sleep(0.1)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await asyncio.wait_for(self._task, timeout=5)

    def has_cycle(self) -> bool:
        """True if some snapshot contains backends that block each other in a loop."""
        for snap in self.history:
            graph = {r["pid"]: set(r["blocked_by"] or []) for r in snap}
            for start in graph:
                seen: set[int] = set()
                stack = [start]
                while stack:
                    node = stack.pop()
                    for nxt in graph.get(node, ()):
                        if nxt == start:
                            return True
                        if nxt not in seen:
                            seen.add(nxt)
                            stack.append(nxt)
        return False

    def waited(self) -> bool:
        return any(r["blocked_by"] for snap in self.history for r in snap)

    def dump(self) -> str:
        lines = []
        for i, snap in enumerate(self.history[-30:]):
            for r in snap:
                lines.append(
                    f"[{i}] pid={r['pid']} app={r['app']} {r['state']} "
                    f"{r['wait_event_type']}/{r['wait_event']} blocked_by={r['blocked_by']} "
                    f"{r['query']!r}"
                )
        return "\n".join(lines)


async def test_two_revisions_reach_the_head_lock_together_without_waiting(world) -> None:
    from sourcemind.services.memory import store as store_module
    from sourcemind.workers import ingestion as worker

    w = world
    _install_retire(w.mp, _local_retire)
    _facts(w.harness, [_fact("rev")])

    original = worker._lock_for_publication
    arrived = 0
    both_here = asyncio.Event()
    release = asyncio.Event()

    async def parked(session, doc, ws_uuid):  # type: ignore[no-untyped-def]
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            both_here.set()
        await release.wait()
        return await original(session, doc, ws_uuid)

    w.mp.setattr(worker, "_lock_for_publication", parked)

    # Tag each backend with its document so a stall names the transaction involved.
    real_status = store_module.update_document_status

    async def tagged_status(session, doc_id, *args, **kwargs):  # type: ignore[no-untyped-def]
        await session.execute(
            text("SELECT set_config('application_name', :n, false)"),
            {"n": f"doc-{str(doc_id)[:8]}"},
        )
        return await real_status(session, doc_id, *args, **kwargs)

    w.mp.setattr(store_module, "update_document_status", tagged_status)

    sampler = Sampler(w.engine)
    sampler.start()
    outs: list[Any] = []
    try:
        sid, d1, d2 = await _two_revisions(w)
        t1 = asyncio.create_task(_run(w.data, d1))
        t2 = asyncio.create_task(_run(w.data, d2))
        try:
            await asyncio.wait_for(both_here.wait(), timeout=15)
        except TimeoutError:
            pytest.fail(
                f"only {arrived} of 2 pipelines reached the head-lock point in 15 s; the "
                "newer revision is waiting on the older one's transaction:\n" + sampler.dump()
            )
        # Reaching the barrier must not have needed any lock wait: the two pipelines are
        # independent until publication.
        assert not sampler.waited(), "pipelines waited on each other:\n" + sampler.dump()
        release.set()
        outs = await asyncio.wait_for(asyncio.gather(t1, t2, return_exceptions=True), timeout=60)
    finally:
        release.set()
        await sampler.stop()

    assert not sampler.has_cycle(), "wait cycle among backends:\n" + sampler.dump()
    assert not [o for o in outs if isinstance(o, BaseException)], outs
    # Whatever the interleaving, the end state is the same and nothing was left half-done.
    assert await _live(w.engine, w.data, d1) == 0
    assert await _live(w.engine, w.data, d2) == 1
    head = await _head(w.engine, w.data, sid)
    assert (head.published_seq, head.published_document_id) == (2, d2)
