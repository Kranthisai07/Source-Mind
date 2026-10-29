"""Graph projector: consumes graph events from Redpanda and applies them to Neo4j.

Run it as its own process (``python -m sourcemind.events.projector``).

Delivery is at-least-once and the apply is idempotent (see events/graph_mapper.py), so the
rules are simple:

* each event is applied in ONE Neo4j transaction;
* offsets are committed only after a batch has been applied or dead-lettered, so a crash
  re-delivers rather than loses;
* a message that cannot be parsed or applied after ``graph_projector_max_attempts`` goes to the
  dead-letter topic with its coordinates and the error, and the stream moves on. A poison
  message must not stall every workspace behind it;
* but "Neo4j is unavailable" is not poison: those failures are retried until Neo4j is back (or
  the projector is stopped, without committing), so an outage never loses events;
* before an event is dead-lettered its workspace is marked as having a gap. The watermark is a
  maximum, so without the mark a later event would lift it past the lost one and the API would
  trust a graph that is missing state. Only a completed backfill clears the mark.

The graph is a projection of PostgreSQL: dead-lettered or lost events are repaired by the
backfill (``python -m sourcemind.events.backfill``), never by editing the graph.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
import uuid
from typing import Any, Protocol

import structlog
from aiokafka import AIOKafkaConsumer
from neo4j.exceptions import ServiceUnavailable, SessionExpired, TransientError

from sourcemind.core.config import get_settings
from sourcemind.core.graph import close_graph, ensure_schema, get_graph_session, init_graph
from sourcemind.events.envelope import EnvelopeError, GraphEvent, parse_envelope
from sourcemind.events.graph_mapper import InvalidEventError, apply_event, mark_gap
from sourcemind.events.producer import GraphEventProducer, ensure_topics

log = structlog.get_logger(__name__)


class DeadLetters(Protocol):
    async def send(self, value: dict[str, Any], key: str | None = None) -> None: ...


async def apply_to_neo4j(event: GraphEvent) -> bool:
    """Apply one event in a single managed (auto-retrying) Neo4j transaction."""

    async def work(tx: Any) -> bool:
        async def run(query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
            result = await tx.run(query, params)
            return [record.data() async for record in result]

        return await apply_event(run, event)

    async with get_graph_session() as session:
        return bool(await session.execute_write(work))


async def mark_gap_in_neo4j(workspace_id: str, seq: int) -> None:
    """Record a lost event for a workspace in one managed Neo4j transaction."""

    async def work(tx: Any) -> None:
        async def run(query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
            result = await tx.run(query, params)
            return [record.data() async for record in result]

        await mark_gap(run, workspace_id, seq)

    async with get_graph_session() as session:
        await session.execute_write(work)


def is_transient(exc: BaseException) -> bool:
    """True when the failure says "Neo4j is unavailable", not "this event is bad"."""
    return isinstance(exc, ServiceUnavailable | SessionExpired | TransientError | OSError)


def _salvage(raw: bytes) -> tuple[str, int] | None:
    """Workspace and seq of a message that failed envelope validation, if it still says."""
    try:
        data = json.loads(raw)
        workspace_id = str(uuid.UUID(str(data["workspace_id"])))
        seq = data.get("seq")
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    valid = isinstance(seq, int) and not isinstance(seq, bool) and seq >= 1
    return workspace_id, seq if valid else 1


async def process_message(
    raw: bytes,
    *,
    coordinates: dict[str, Any],
    apply: Any,
    dead_letters: DeadLetters,
    max_attempts: int,
    mark_gap: Any,
    stop: asyncio.Event | None = None,
    sleep: Any = asyncio.sleep,
) -> str:
    """Handle one message. Returns "applied", "stale", "dead_lettered" or "interrupted".

    "interrupted" means the projector was told to stop while Neo4j was unavailable: the caller
    must not commit offsets, so the message is delivered again. Raises when the dead-letter topic
    or the gap mark cannot be written, for the same reason.
    """
    try:
        event = parse_envelope(raw)
    except EnvelopeError as exc:
        await _dead_letter(
            dead_letters, raw, coordinates, f"envelope: {exc}", attempts=0, mark_gap=mark_gap
        )
        return "dead_lettered"

    last_error = ""
    attempt = 0
    while True:
        attempt += 1
        try:
            return "applied" if await apply(event) else "stale"
        except InvalidEventError as exc:
            # Deterministic: retrying cannot help.
            last_error = f"invalid: {exc}"
            break
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            transient = is_transient(exc)
            log.warning(
                "graph.apply_failed",
                seq=event.seq,
                attempt=attempt,
                transient=transient,
                error=last_error,
            )
            if not transient and attempt >= max_attempts:
                break
            if stop is not None and stop.is_set():
                return "interrupted"
            await sleep(min(10.0, 0.5 * 2 ** (min(attempt, 6) - 1)))
    await _dead_letter(
        dead_letters,
        raw,
        coordinates,
        last_error,
        attempts=attempt,
        event=event,
        mark_gap=mark_gap,
    )
    return "dead_lettered"


async def _dead_letter(
    dead_letters: DeadLetters,
    raw: bytes,
    coordinates: dict[str, Any],
    error: str,
    *,
    attempts: int,
    mark_gap: Any,
    event: GraphEvent | None = None,
) -> None:
    # The gap is recorded BEFORE the message leaves the stream. If it cannot be recorded this
    # raises, offsets stay uncommitted and the message is delivered again.
    target = (event.workspace_id, event.seq) if event is not None else _salvage(raw)
    if target is not None:
        try:
            await mark_gap(*target)
        except InvalidEventError:
            # The envelope parsed but its workspace id is not a UUID, so no workspace can be
            # marked. It must still reach the dead-letter topic: letting this escape would end
            # the projector and replay the same poison message on every restart. Genuine Neo4j
            # failures are not InvalidEventError and still propagate.
            target = None
    if target is None:
        log.error("graph.dead_letter_without_workspace", **coordinates)
    log.error(
        "graph.dead_lettered",
        error=error,
        seq=event.seq if event else None,
        workspace_id=event.workspace_id if event else None,
        **coordinates,
    )
    await dead_letters.send(
        {
            "error": error,
            "attempts": attempts,
            "source": coordinates,
            "raw": raw.decode("utf-8", errors="replace"),
        },
        key=event.workspace_id if event else None,
    )


async def run(stop: asyncio.Event | None = None) -> int:
    """Projector loop. Returns a process exit code."""
    settings = get_settings()
    stop = stop or asyncio.Event()

    await init_graph()
    try:
        await ensure_schema()
        await ensure_topics()
        consumer = AIOKafkaConsumer(
            settings.kafka_graph_topic,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=settings.kafka_consumer_group,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            # The default (5 minutes) is how long a consumer would wait to notice a topic that
            # was created after it subscribed.
            metadata_max_age_ms=30_000,
        )
        await consumer.start()
        try:
            async with GraphEventProducer(topic=settings.kafka_graph_dlq_topic) as dead_letters:
                log.info("graph.projector_started", topic=settings.kafka_graph_topic)
                while not stop.is_set():
                    batches = await consumer.getmany(timeout_ms=1000, max_records=100)
                    if not batches:
                        continue
                    counts = {"applied": 0, "stale": 0, "dead_lettered": 0}
                    for partition, messages in batches.items():
                        for message in messages:
                            outcome = await process_message(
                                message.value,
                                coordinates={
                                    "topic": partition.topic,
                                    "partition": partition.partition,
                                    "offset": message.offset,
                                },
                                apply=apply_to_neo4j,
                                dead_letters=dead_letters,
                                max_attempts=settings.graph_projector_max_attempts,
                                mark_gap=mark_gap_in_neo4j,
                                stop=stop,
                            )
                            if outcome == "interrupted":
                                # Stopping mid-outage: commit nothing, redeliver on restart.
                                log.info("graph.projector_interrupted")
                                return 0
                            counts[outcome] += 1
                    # Only after the whole batch is applied or dead-lettered.
                    await consumer.commit()
                    log.info("graph.projector_batch", **counts)
        finally:
            await consumer.stop()
    finally:
        await close_graph()
    log.info("graph.projector_stopped")
    return 0


async def _main() -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows event loops
            loop.add_signal_handler(sig, stop.set)
    return await run(stop)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
