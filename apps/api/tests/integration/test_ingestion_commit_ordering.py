"""The document must be committed before its ingestion task is dispatched.

WHAT BROKE
----------
receive() flushed the Document, dispatched the Celery task, and left the commit
to the request teardown in core/database.py::get_db_session — which runs after
receive() returns, after the route returns, and after the response is
serialised. The worker is a separate process on a separate connection and picks
the task up in milliseconds, so its SELECT found nothing, it logged
pipeline_doc_not_found and returned early WITHOUT touching the row.

The document was then stranded at 'pending'/'queued' with error_message NULL,
which is indistinguishable from "still waiting". A 20-item evaluation ingestion
lost every single item this way: the worker reported 29 tasks received and 0
documents completed.

HOW THIS IS TESTED
------------------
The invariant is not "the row exists once receive() returns" — that was true
before the fix too, by the time anyone looked. The invariant is "the row is
visible to a DIFFERENT connection at the instant the task is dispatched". So
apply_async is patched, and the check runs from inside that call, on its own
connection, in a separate thread with its own event loop.

These tests COMMIT. They clean up after themselves, and the Celery dispatch is
patched out so no real ingestion work is queued.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


def _live_db() -> bool:
    from sourcemind.core.config import get_settings

    url = get_settings().database_url or ""
    return bool(url) and "localhost" not in url and "127.0.0.1" not in url


requires_live_db = pytest.mark.skipif(
    not _live_db(), reason="needs a real (non-local) Postgres to commit against"
)

WORKSPACE_ID = uuid.UUID("00000000-0000-4000-8000-000000000010")
USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


@pytest_asyncio.fixture
async def committing_engine():
    """A genuinely committing engine.

    The standard db_session fixture wraps each test in an outer transaction and
    rolls it back, turning every commit into a savepoint release. Nothing
    outside that connection can see the rows, which makes it useless for a test
    about cross-connection visibility.
    """
    from sourcemind.core.config import get_settings

    # Sized for the burst test, which opens 20 concurrent sessions. At 5+5 the
    # pool was exhausted and the test failed with a QueuePool timeout rather
    # than on the property under test - a defect in the scaffolding, not in
    # commit ordering. It only surfaced once Railway latency rose enough for
    # connections to be held longer.
    engine = create_async_engine(
        get_settings().async_database_url,
        pool_size=25,
        max_overflow=10,
        pool_pre_ping=True,
        connect_args={"statement_cache_size": 0},
    )
    try:
        yield engine
    finally:
        await engine.dispose()


# The visibility checks are serialised. Each opens its own connection, and with
# twenty submissions in flight that meant ~20 simultaneous extra connections to
# Railway on top of the session pool - enough to trip the proxy (WinError 121).
#
# Serialising does not weaken the assertion. The check runs inside apply_async,
# and under the OLD code the commit happened at request teardown, long after
# every dispatch in the burst; a few milliseconds of queueing cannot make an
# uncommitted row appear. Confirmed by the revert check: without the fix the
# burst still fails 20/20.
_CHECK_LOCK = threading.Lock()


def _visible_from_another_connection(document_id: str) -> bool:
    """True if `document_id` is committed and visible elsewhere, right now.

    Runs in its own thread with its own event loop and its own engine, so it
    shares nothing with the session under test — no pool entry, no
    transaction, no session identity map.
    """

    async def check() -> bool:
        from sourcemind.core.config import get_settings

        # NullPool explicitly: poolclass=None means "use the default", which
        # would pool connections this short-lived engine never reuses. Each
        # check runs in its own thread and loop, so the connection must be
        # released the moment it is done.
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(
            get_settings().async_database_url,
            poolclass=NullPool,
            connect_args={"statement_cache_size": 0},
        )
        try:
            async with engine.connect() as conn:
                found = (
                    await conn.execute(
                        text(
                            "SELECT 1 FROM documents WHERE id = CAST(:d AS uuid)"
                        ),
                        {"d": document_id},
                    )
                ).first()
                return found is not None
        finally:
            await engine.dispose()

    with _CHECK_LOCK:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(check())).result(timeout=60)


async def _cleanup(engine, document_ids: list[str]) -> None:
    if not document_ids:
        return
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM documents WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": document_ids},
        )


class _DispatchSpy:
    """Stands in for process_document.apply_async.

    Records, at the moment of dispatch, whether the document was already
    visible to an unrelated connection. That is the property under test.
    """

    def __init__(self) -> None:
        self.visible_at_dispatch: list[bool] = []
        self.document_ids: list[str] = []

    def __call__(self, *args, **kwargs):
        document_id = kwargs.get("kwargs", {}).get("document_id")
        self.document_ids.append(document_id)
        self.visible_at_dispatch.append(
            _visible_from_another_connection(document_id)
        )

        class _Result:
            id = str(uuid.uuid4())

        return _Result()


@pytest.mark.integration
@pytest.mark.asyncio
@requires_live_db
async def test_document_is_committed_before_its_task_is_dispatched(
    committing_engine, monkeypatch
):
    """One document. Must be visible elsewhere the instant it is dispatched."""
    from sourcemind.core.redis_client import init_redis
    from sourcemind.services.ingestion import receiver
    from sourcemind.workers import ingestion as worker

    await init_redis()
    spy = _DispatchSpy()
    monkeypatch.setattr(worker.process_document, "apply_async", spy)

    created: list[str] = []
    try:
        async with AsyncSession(committing_engine, expire_on_commit=False) as session:
            result = await receiver.receive(
                session=session,
                workspace_id=WORKSPACE_ID,
                user_id=USER_ID,
                content=f"commit-ordering probe {uuid.uuid4()}",
                url=None,
                source_type="text",
                title="ordering probe",
                tags=[],
                idempotency_key=str(uuid.uuid4()),
            )
        created.append(result["document_id"])

        assert spy.visible_at_dispatch == [True], (
            "the ingestion task was dispatched while the document was still "
            "uncommitted — the worker would race it and find nothing"
        )
    finally:
        await _cleanup(committing_engine, created)


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.asyncio
@requires_live_db
async def test_a_rapid_burst_commits_every_document_before_dispatch(
    committing_engine, monkeypatch
):
    """Twenty submissions in flight at once, which is what broke in practice.

    A single-document test can pass on timing alone. The evaluation harness
    submits in a tight loop, and that is the shape that lost 20 of 20, so the
    burst is the case that has to hold.
    """
    from sourcemind.core.redis_client import init_redis
    from sourcemind.services.ingestion import receiver
    from sourcemind.workers import ingestion as worker

    await init_redis()
    spy = _DispatchSpy()
    monkeypatch.setattr(worker.process_document, "apply_async", spy)

    burst = 20
    created: list[str] = []

    async def submit(n: int) -> str:
        # A session each: AsyncSession is not safe to share across tasks.
        async with AsyncSession(committing_engine, expire_on_commit=False) as session:
            result = await receiver.receive(
                session=session,
                workspace_id=WORKSPACE_ID,
                user_id=USER_ID,
                content=f"burst probe {n} {uuid.uuid4()}",
                url=None,
                source_type="text",
                title=f"burst probe {n}",
                tags=[],
                idempotency_key=str(uuid.uuid4()),
            )
            return str(result["document_id"])

    try:
        created = list(await asyncio.gather(*(submit(n) for n in range(burst))))

        assert len(spy.visible_at_dispatch) == burst, (
            f"expected {burst} dispatches, saw {len(spy.visible_at_dispatch)}"
        )
        invisible = spy.visible_at_dispatch.count(False)
        assert invisible == 0, (
            f"{invisible}/{burst} documents were dispatched before being "
            "committed — the worker would lose exactly those"
        )
    finally:
        await _cleanup(committing_engine, created)
