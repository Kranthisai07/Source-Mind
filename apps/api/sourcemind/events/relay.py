"""Graph outbox relay: publishes ``graph_outbox`` rows to Redpanda, in ``seq`` order.

Run it as its own process (``python -m sourcemind.events.relay``). It connects with
``GRAPH_RELAY_DATABASE_URL``, a role that may only SELECT from ``graph_outbox`` and UPDATE
its ``published_at`` (migration 20261029_0015), so it spans every workspace without any
row-level-security bypass.

Delivery is at-least-once: a crash after a send but before ``published_at`` is written
re-sends that event. That is harmless, because events are full state ordered by ``seq`` and
the projector skips anything it has already applied.

A single instance runs at a time, enforced with a session-level PostgreSQL advisory lock, so
publication order is the ``seq`` order. Rows younger than ``graph_relay_settle_seconds`` are
left for the next poll: ``seq`` is assigned at insert, not commit, so a slower concurrent
transaction could otherwise commit a lower ``seq`` after a higher one was published.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from sourcemind.core.config import get_settings
from sourcemind.events.envelope import EVENT_MEMORY_STATE, build_envelope
from sourcemind.events.producer import GraphEventProducer, ensure_topics

log = structlog.get_logger(__name__)

#: Session-level advisory lock key ("SGRL"). One relay at a time.
ADVISORY_LOCK_KEY = 0x5347524C
LAG_LOG_INTERVAL_SECONDS = 30.0


class Publisher(Protocol):
    async def publish(self, envelope: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class OutboxRow:
    seq: int
    event_id: str
    workspace_id: str
    event_type: str
    aggregate_id: str
    payload: dict[str, Any]
    created_at: datetime


def to_envelope(row: OutboxRow) -> dict[str, Any]:
    return build_envelope(
        event_id=row.event_id,
        seq=row.seq,
        occurred_at=row.created_at,
        workspace_id=row.workspace_id,
        event_type=row.event_type or EVENT_MEMORY_STATE,
        aggregate_id=row.aggregate_id,
        payload=row.payload,
    )


async def fetch_batch(
    conn: AsyncConnection, batch_size: int, settle_seconds: float
) -> list[OutboxRow]:
    result = await conn.execute(
        text(
            "SELECT seq, event_id::text AS event_id, workspace_id::text AS workspace_id, "
            "event_type, aggregate_id::text AS aggregate_id, payload, created_at "
            "FROM graph_outbox "
            "WHERE published_at IS NULL "
            "AND created_at < now() - make_interval(secs => :settle) "
            "ORDER BY seq LIMIT :limit"
        ),
        {"settle": settle_seconds, "limit": batch_size},
    )
    return [OutboxRow(**row) for row in result.mappings().all()]


async def mark_published(conn: AsyncConnection, seqs: Sequence[int]) -> None:
    """Mark rows published and advance each workspace's cursor, in one transaction.

    The cursor (``graph_publication_cursor``) is what lets the API tell a fresh graph from a
    lagging one: the projector's applied seq must reach it. ``GREATEST`` keeps it monotonic.
    """
    await conn.execute(
        text(
            "UPDATE graph_outbox SET published_at = now() "
            "WHERE seq = ANY(CAST(:seqs AS bigint[])) AND published_at IS NULL"
        ),
        {"seqs": list(seqs)},
    )
    await conn.execute(
        text(
            "INSERT INTO graph_publication_cursor (workspace_id, published_seq) "
            "SELECT workspace_id, max(seq) FROM graph_outbox "
            "WHERE seq = ANY(CAST(:seqs AS bigint[])) GROUP BY workspace_id "
            "ON CONFLICT (workspace_id) DO UPDATE SET "
            "published_seq = GREATEST(graph_publication_cursor.published_seq, "
            "EXCLUDED.published_seq), updated_at = now()"
        ),
        {"seqs": list(seqs)},
    )


async def publish_pending(
    conn: AsyncConnection, producer: Publisher, *, batch_size: int, settle_seconds: float
) -> int:
    """Publish one batch in order. Stops at the first failure so order is never skipped.

    Rows acknowledged before a failure are still marked published, then the error propagates.
    """
    rows = await fetch_batch(conn, batch_size, settle_seconds)
    published: list[int] = []
    try:
        for row in rows:
            await producer.publish(to_envelope(row))
            published.append(row.seq)
    finally:
        if published:
            await mark_published(conn, published)
        await conn.commit()
    return len(published)


async def log_lag(conn: AsyncConnection) -> None:
    row = (
        await conn.execute(
            text(
                "SELECT count(*) AS pending, "
                "COALESCE(EXTRACT(EPOCH FROM now() - min(created_at)), 0) AS oldest_seconds "
                "FROM graph_outbox WHERE published_at IS NULL"
            )
        )
    ).mappings().one()
    await conn.commit()
    log.info(
        "graph.relay_lag",
        pending=int(row["pending"]),
        oldest_seconds=round(float(row["oldest_seconds"]), 1),
    )


async def acquire_singleton(conn: AsyncConnection) -> bool:
    acquired = (
        await conn.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
        )
    ).scalar()
    await conn.commit()
    return bool(acquired)


async def run(stop: asyncio.Event | None = None) -> int:
    """Relay loop. Returns a process exit code."""
    settings = get_settings()
    stop = stop or asyncio.Event()
    if not settings.graph_relay_database_url:
        log.error("graph.relay_not_configured", hint="set GRAPH_RELAY_DATABASE_URL")
        return 2

    await ensure_topics()
    engine = create_async_engine(settings.graph_relay_database_url, pool_size=1, max_overflow=0)
    failures = 0
    last_lag_log = 0.0
    try:
        async with engine.connect() as conn, GraphEventProducer() as producer:
            if not await acquire_singleton(conn):
                log.error("graph.relay_already_running")
                return 3
            log.info("graph.relay_started", batch=settings.graph_relay_batch_size)
            while not stop.is_set():
                try:
                    sent = await publish_pending(
                        conn,
                        producer,
                        batch_size=settings.graph_relay_batch_size,
                        settle_seconds=settings.graph_relay_settle_seconds,
                    )
                    failures = 0
                except Exception as exc:
                    # Includes broker outages. Rows stay unpublished and are retried.
                    failures += 1
                    delay = min(30.0, 2.0**failures)
                    log.error("graph.relay_publish_failed", error=str(exc), retry_in=delay)
                    with contextlib.suppress(Exception):
                        await conn.rollback()
                    await _sleep(stop, delay)
                    continue
                if time.monotonic() - last_lag_log >= LAG_LOG_INTERVAL_SECONDS:
                    last_lag_log = time.monotonic()
                    with contextlib.suppress(Exception):
                        await log_lag(conn)
                if sent == 0:
                    await _sleep(stop, settings.graph_relay_poll_seconds)
    finally:
        await engine.dispose()
    log.info("graph.relay_stopped")
    return 0


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)


async def _main() -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows event loops
            loop.add_signal_handler(sig, stop.set)
    return await run(stop)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
