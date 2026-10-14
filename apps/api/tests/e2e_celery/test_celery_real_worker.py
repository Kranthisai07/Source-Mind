"""Real Celery worker + real Redis broker + real PostgreSQL (disposable); GitHub and providers stubbed.

Run only on purpose:  CELERY_E2E=1 pytest tests/e2e_celery  (inside the disposable-DB environment).
Not part of the Security Acceptance gate: it starts operating-system processes and takes minutes.

What is real: ``celery worker`` / ``celery beat`` processes (prefork, acks_late, task routes, the
Redis broker with a short visibility timeout), the application code, PostgreSQL under FORCE RLS as the
restricted runtime role, Redis. What is SIMULATED: GitHub (in-memory fake behind the real client), the
model providers, GitHub App auth and the repository verifier (see ``e2e_app.py``).
The lease-expiry step moves the lease deadline in the database instead of waiting out the lease; that
one time shift is labelled where it happens.
"""

# ruff: noqa: F811

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from tests.integration.test_connector_sync_real_db import (  # noqa: F401 - fixtures/helpers
    _make_connector,
    _open,
    _rows,
    world,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(os.getenv("CELERY_E2E") != "1", reason="set CELERY_E2E=1 to run"),
]

API_DIR = Path(__file__).resolve().parents[2]
WORK = Path(os.getenv("E2E_WORKDIR", "/tmp/sm-celery-e2e"))
BROKER_DB = 14
CONNECTOR_DOCS = "SELECT count(*) FROM documents WHERE connector_id = CAST(:c AS uuid)"
COMPLETED_DOCS = CONNECTOR_DOCS + " AND ingestion_status = 'completed'"
MEMORIES = "SELECT count(*) FROM memories WHERE workspace_id = CAST(:w AS uuid)"


def _redis_url() -> str:
    return os.environ["TEST_REDIS_URL"].rsplit("/", 1)[0] + f"/{BROKER_DB}"


def _worker_env(**extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(API_DIR),
            "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
            "REDIS_URL": _redis_url(),
            "ENVIRONMENT": "development",
            "DEBUG": "false",
            "AUTH_DEV_BYPASS_ENABLED": "false",
            "CLERK_SECRET_KEY": "sk_test_e2e",
            "CLERK_PUBLISHABLE_KEY": "pk_test_Y2xlcmsuZTJlLnRlc3Qk",
            "CLERK_AUTHORIZED_PARTIES": '["https://app.e2e.test"]',
            "CORS_ORIGINS": '["https://app.e2e.test"]',
            "URL_INGESTION_ENABLED": "false",
            "SLACK_MEMORY_COMMANDS_ENABLED": "false",
            "RATE_LIMIT_FAIL_CLOSED": "true",
            "OPENAI_API_KEY": "not-sent",
            "ANTHROPIC_API_KEY": "not-sent",
            "FF_NEO4J_ATTRIBUTION": "false",
            "FF_KAFKA_EVENTS": "false",
            "GITHUB_APP_PRIVATE_KEY_PEM": "stub",
            "GITHUB_APP_ID": "1",
            "E2E_VISIBILITY_TIMEOUT": "5",
            "E2E_PROVIDER_LOG": str(WORK / "provider_calls.log"),
            "E2E_PROVIDER_HANG": str(WORK / "provider_hang"),
            "E2E_GITHUB_HANG": str(WORK / "github_hang"),
            "CONNECTOR_SCHEDULER_ENABLED": "false",
        }
    )
    env.update(extra)
    return env


class Proc:
    """A celery worker/beat child process in its own process group (so a kill takes the pool)."""

    def __init__(self, name: str, args: list[str], env: dict[str, str]) -> None:
        self.name = name
        self.log_path = WORK / f"{name}.log"
        self.log = self.log_path.open("w", encoding="utf-8")
        self.p = subprocess.Popen(  # noqa: S603 - fixed argv, disposable test environment
            [sys.executable, "-m", "celery", "-A", "tests.e2e_celery.e2e_app:app", *args],
            cwd=API_DIR, env=env, stdout=self.log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def text(self) -> str:
        self.log.flush()
        return self.log_path.read_text(encoding="utf-8", errors="replace")

    async def wait_for(self, needle: str, timeout: float = 60) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if needle in self.text():
                return
            if self.p.poll() is not None:
                break
            await asyncio.sleep(0.3)
        raise AssertionError(f"{self.name}: {needle!r} not seen\n{self.text()[-3000:]}")

    def kill(self) -> None:
        """SIGKILL the whole group: no warm shutdown, no ack, no cleanup."""
        if self.p.poll() is None:
            os.killpg(self.p.pid, signal.SIGKILL)
            self.p.wait(timeout=10)


@pytest.fixture
def procs():
    WORK.mkdir(parents=True, exist_ok=True)
    for f in WORK.glob("*"):
        if f.is_file():
            f.unlink()
    started: list[Proc] = []
    yield started
    for p in started:
        p.kill()


async def _worker(procs: list[Proc], name: str, queues: str) -> Proc:
    pr = Proc(
        name,
        ["worker", "-Q", queues, "-c", "1", "-P", "prefork", "-l", "INFO", "--without-gossip",
         "--without-mingle", "--without-heartbeat"],
        _worker_env(),
    )
    procs.append(pr)
    await pr.wait_for("ready.")
    return pr


@pytest.fixture(autouse=True)
def broker(monkeypatch):
    """A clean broker database, and the test process publishing to it like any producer."""
    import redis

    r = redis.Redis.from_url(_redis_url(), decode_responses=True)
    r.flushdb()
    monkeypatch.setenv("REDIS_URL", _redis_url())
    from sourcemind.core.config import get_settings

    get_settings.cache_clear()
    from sourcemind.workers.celery_app import app

    app.conf.broker_url = _redis_url()
    app.conf.broker_transport_options = {"visibility_timeout": 5}
    yield r
    r.flushdb()
    get_settings.cache_clear()


def _queued_ids(r, queue: str) -> list[str]:
    """document_id of every message waiting in *queue* (the Redis transport appends a priority
    suffix to the list name, and a body is base64 JSON ``[args, kwargs, embed]``)."""
    import base64
    import json

    ids: list[str] = []
    for key in r.keys(f"{queue}*"):
        if key != queue and not key.startswith(f"{queue}"):
            continue
        for raw in r.lrange(key, 0, -1):
            body = json.loads(base64.b64decode(json.loads(raw)["body"]))
            ids.append(str(body[1].get("document_id")))
    return ids


async def _my_docs(engine, data, cid) -> set[str]:
    rows = await _rows(
        engine, data, "SELECT id FROM documents WHERE connector_id = CAST(:c AS uuid)", c=str(cid))
    return {str(r.id) for r in rows}


async def _wait(cond, what: str, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await cond():
            return
        await asyncio.sleep(0.5)
    raise AssertionError(f"timed out waiting for: {what}")


async def _scalar(engine, data, sql: str, **params):
    return (await _rows(engine, data, sql, **params))[0][0]


def _send_sync(cid, data, trigger: str = "manual"):
    from sourcemind.workers.connector_tasks import sync_connector

    return sync_connector.apply_async(
        kwargs={
            "connector_id": str(cid), "workspace_id": str(data.target_workspace_id),
            "user_id": str(data.admin_id), "trigger": trigger,
        }
    )


async def _handed_off(engine, data, cid, n: int = 3) -> None:
    async def ok():
        return await _scalar(engine, data, CONNECTOR_DOCS, c=str(cid)) == n

    await _wait(ok, f"{n} documents handed off")


# ── 1. beat schedules, the connectors queue syncs, the ingestion queue is separate ────────


async def test_beat_schedules_sync_and_routing_separates_queues(world, procs, broker) -> None:
    engine, data = world
    cid = await _make_connector(engine, data, next_sync_at=datetime.now(UTC) - timedelta(minutes=1))
    ws = str(data.target_workspace_id)
    base = await _scalar(engine, data, MEMORIES, w=ws)  # the tenant seed already has memories

    # A connectors-only worker and a real beat process; the ingestion queue has no consumer yet.
    await _worker(procs, "w_connectors", "connectors")
    beat = Proc(
        "beat",
        ["beat", "-l", "INFO", "-s", str(WORK / "beat-schedule")],
        _worker_env(CONNECTOR_SCHEDULER_ENABLED="true", CONNECTOR_SYNC_TICK_SECONDS="3"),
    )
    procs.append(beat)

    await _handed_off(engine, data, cid)
    logs = await _rows(
        engine, data,
        "SELECT trigger, status FROM connector_sync_logs WHERE connector_id = CAST(:c AS uuid)",
        c=str(cid),
    )
    assert logs and logs[0].trigger == "scheduled", logs
    assert "dispatch_due_syncs" in beat.text()

    # Routing: the three process_document messages wait in the 'ingestion' queue, untouched by the
    # connectors worker, and nothing has been published as a memory.
    mine = await _my_docs(engine, data, cid)
    queued = _queued_ids(broker, "ingestion")
    assert len(mine) == 3 and sorted(i for i in queued if i in mine) == sorted(mine), queued
    assert await _scalar(engine, data, MEMORIES, w=ws) == base

    # An ingestion consumer appears and drains exactly those messages.
    w_ing = await _worker(procs, "w_ingestion", "ingestion,default")

    async def done():
        return await _scalar(engine, data, COMPLETED_DOCS, c=str(cid)) == 3

    await _wait(done, "3 documents completed by the ingestion worker")
    assert not [i for i in _queued_ids(broker, "ingestion") if i in mine]
    assert await _scalar(engine, data, MEMORIES, w=ws) == base + 3
    assert "process_document" in w_ing.text() and "succeeded" in w_ing.text()
    assert "sync_connector" not in w_ing.text().split("ready.", 1)[1]  # never ran a sync task


# ── 2. duplicate delivery ────────────────────────────────────────────────────────────────


async def test_duplicate_delivery_creates_one_memory(world, procs, broker) -> None:
    from sourcemind.workers.ingestion import process_document

    engine, data = world
    cid = await _make_connector(engine, data)
    await _worker(procs, "w_connectors", "connectors")
    _send_sync(cid, data)
    await _handed_off(engine, data, cid)

    doc = (await _rows(
        engine, data,
        "SELECT id FROM documents WHERE connector_id = CAST(:c AS uuid) ORDER BY created_at LIMIT 1",
        c=str(cid),
    ))[0].id
    # The same message delivered twice more (a broker redelivery looks exactly like this).
    for _ in range(2):
        process_document.apply_async(kwargs={
            "document_id": str(doc), "workspace_id": str(data.target_workspace_id),
            "user_id": str(data.admin_id)})
    mine = await _my_docs(engine, data, cid)
    assert sorted(i for i in _queued_ids(broker, "ingestion") if i in mine) == sorted(
        [*mine, str(doc), str(doc)])  # three originals plus the two extra deliveries

    w_ing = await _worker(procs, "w_ingestion", "ingestion")

    async def drained():
        return not [i for i in _queued_ids(broker, "ingestion") if i in mine] and await _scalar(
            engine, data, COMPLETED_DOCS, c=str(cid)) == 3

    await _wait(drained, "all five deliveries consumed")
    await asyncio.sleep(1)
    assert await _scalar(
        engine, data, "SELECT count(*) FROM memories WHERE document_id = CAST(:d AS uuid)",
        d=str(doc),
    ) == 1
    assert w_ing.text().count(f"'document_id': '{doc}'") >= 3  # delivered 3 times, 1 memory


# ── 3. a worker is killed mid-task: acks_late redelivery finishes it exactly once ─────────


async def test_killed_ingestion_worker_task_is_redelivered_and_completes_once(
    world, procs, broker
) -> None:
    engine, data = world
    cid = await _make_connector(engine, data)
    base = await _scalar(engine, data, MEMORIES, w=str(data.target_workspace_id))
    await _worker(procs, "w_connectors", "connectors")
    _send_sync(cid, data)
    await _handed_off(engine, data, cid)

    (WORK / "provider_hang").write_text("x")  # the embedding call will wait
    w1 = await _worker(procs, "w_ing_1", "ingestion")

    async def in_flight():
        log = WORK / "provider_calls.log"
        return log.exists() and "embed" in log.read_text()

    await _wait(in_flight, "a task reached the provider call")
    w1.kill()  # SIGKILL: the message was received but never acknowledged
    (WORK / "provider_hang").unlink()
    await _worker(procs, "w_ing_2", "ingestion")  # the visibility timeout (5 s) requeues it

    async def all_done():
        return await _scalar(engine, data, COMPLETED_DOCS, c=str(cid)) == 3

    await _wait(all_done, "redelivered work completed", timeout=150)
    assert await _scalar(engine, data, MEMORIES, w=str(data.target_workspace_id)) == base + 3  # no dupes


# ── 4. a sync worker dies holding the lease ───────────────────────────────────────────────


async def test_killed_sync_worker_lease_blocks_until_expiry_then_takeover(world, procs, broker) -> None:
    engine, data = world
    cid = await _make_connector(engine, data)
    (WORK / "github_hang").write_text("x")
    w1 = await _worker(procs, "w_sync_1", "connectors")
    _send_sync(cid, data)

    async def holding():
        return bool(await _scalar(
            engine, data,
            "SELECT sync_lease_expires_at > now() FROM connector_configs WHERE id = CAST(:c AS uuid)",
            c=str(cid),
        ))

    await _wait(holding, "sync holds the lease while stuck on GitHub")
    fence_before = await _scalar(
        engine, data, "SELECT fence FROM connector_configs WHERE id = CAST(:c AS uuid)", c=str(cid))
    w1.kill()
    (WORK / "github_hang").unlink()

    # Redelivery reaches a new worker, but the dead worker's lease has not expired: no second run.
    await _worker(procs, "w_sync_2", "connectors")
    await asyncio.sleep(12)  # longer than the visibility timeout
    statuses = [r.status for r in await _rows(
        engine, data,
        "SELECT status FROM connector_sync_logs WHERE connector_id = CAST(:c AS uuid)", c=str(cid))]
    assert "completed" not in statuses, statuses
    assert await _scalar(engine, data, CONNECTOR_DOCS, c=str(cid)) == 0

    # TIME SHIFT (labelled): the lease deadline is moved into the past instead of waiting 10 min.
    s = await _open(engine, data.admin_id, data.target_workspace_id)
    async with s:
        await s.execute(
            text("UPDATE connector_configs SET sync_lease_expires_at = now() - interval '1 second' "
                 "WHERE id = CAST(:c AS uuid)"),
            {"c": str(cid)},
        )
        await s.commit()
    _send_sync(cid, data)

    async def recovered():
        rows = await _rows(
            engine, data,
            "SELECT status FROM connector_sync_logs WHERE connector_id = CAST(:c AS uuid)",
            c=str(cid))
        return any(r.status == "completed" for r in rows)

    await _wait(recovered, "a new worker took the expired lease and completed", timeout=60)
    statuses = sorted(r.status for r in await _rows(
        engine, data,
        "SELECT status FROM connector_sync_logs WHERE connector_id = CAST(:c AS uuid)", c=str(cid)))
    assert "interrupted" in statuses and "completed" in statuses, statuses  # the stale run is reaped
    assert "running" not in statuses
    fence_after = await _scalar(
        engine, data, "SELECT fence FROM connector_configs WHERE id = CAST(:c AS uuid)", c=str(cid))
    assert fence_after > fence_before


# ── 5. disconnect while documents are queued ──────────────────────────────────────────────


async def test_disconnect_with_queued_documents_stops_all_provider_work(world, procs, broker) -> None:
    from sourcemind.services.connectors import lifecycle
    from sourcemind.services.memory.retire import retire_document_memories

    engine, data = world
    cid = await _make_connector(engine, data)
    base = await _scalar(engine, data, MEMORIES, w=str(data.target_workspace_id))
    await _worker(procs, "w_connectors", "connectors")
    _send_sync(cid, data)
    await _handed_off(engine, data, cid)
    mine = await _my_docs(engine, data, cid)
    assert sorted(i for i in _queued_ids(broker, "ingestion") if i in mine) == sorted(mine)

    s = await _open(engine, data.admin_id, data.target_workspace_id)
    async with s:
        await lifecycle.disconnect(s, cid, remove_memories=False, retire=retire_document_memories)
        await s.commit()

    await _worker(procs, "w_ingestion", "ingestion")

    async def drained():
        failed = CONNECTOR_DOCS + " AND ingestion_status = 'failed'"
        return not [i for i in _queued_ids(broker, "ingestion") if i in mine] and await _scalar(
            engine, data, failed, c=str(cid)) == 3

    await _wait(drained, "queued documents terminated")
    codes = {r.code for r in await _rows(
        engine, data,
        "SELECT metadata->>'error_code' AS code FROM documents WHERE connector_id = CAST(:c AS uuid)",
        c=str(cid))}
    assert codes == {"connector_disconnected"}, codes
    assert await _scalar(engine, data, MEMORIES, w=str(data.target_workspace_id)) == base
    calls = WORK / "provider_calls.log"
    assert not calls.exists() or calls.read_text() == "", "a provider call happened after disconnect"
