"""Projector failure handling, relay ordering, and backfill batching, against fakes."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from neo4j.exceptions import ServiceUnavailable, SessionExpired, TransientError

from sourcemind.events import backfill, graph_mapper, projector, relay
from sourcemind.events.envelope import build_envelope, dumps
from sourcemind.events.graph_mapper import InvalidEventError
from tests.unit.events.fakes import RoutedSession, Rows

WS = str(uuid.uuid4())
COORDS = {"topic": "t", "partition": 0, "offset": 5}


def _raw(seq: int = 3) -> bytes:
    return dumps(
        build_envelope(
            event_id=uuid.uuid4(),
            seq=seq,
            occurred_at=datetime(2026, 10, 29, tzinfo=UTC),
            workspace_id=WS,
            event_type="memory.state",
            aggregate_id=uuid.uuid4(),
            payload={"memory": {"id": str(uuid.uuid4())}},
        )
    )


class DeadLetters:
    def __init__(self) -> None:
        self.sent: list[tuple[dict, str | None]] = []

    async def send(self, value, key=None) -> None:
        self.sent.append((value, key))


class MarkGap:
    """Records (workspace_id, seq) of every gap marked, in order."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.marked: list[tuple[str, int]] = []
        self.fail = fail

    async def __call__(self, workspace_id: str, seq: int) -> None:
        if self.fail:
            raise self.fail
        self.marked.append((workspace_id, seq))


async def _no_sleep(_seconds: float) -> None:
    return None


# ── projector ────────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_a_good_event_is_applied_and_nothing_is_dead_lettered() -> None:
    dlq = DeadLetters()
    outcome = await projector.process_message(
        _raw(),
        coordinates=COORDS,
        apply=AsyncMock(return_value=True),
        dead_letters=dlq,
        max_attempts=3,
        mark_gap=MarkGap(),
        sleep=_no_sleep,
    )
    assert outcome == "applied" and dlq.sent == []


@pytest.mark.unit
async def test_a_stale_event_is_reported_as_stale() -> None:
    outcome = await projector.process_message(
        _raw(),
        coordinates=COORDS,
        apply=AsyncMock(return_value=False),
        dead_letters=DeadLetters(),
        max_attempts=3,
        mark_gap=MarkGap(),
        sleep=_no_sleep,
    )
    assert outcome == "stale"


@pytest.mark.unit
async def test_an_unparseable_message_is_dead_lettered_without_applying() -> None:
    dlq, apply = DeadLetters(), AsyncMock()
    outcome = await projector.process_message(
        b"{not json",
        coordinates=COORDS,
        apply=apply,
        dead_letters=dlq,
        max_attempts=3,
        mark_gap=MarkGap(),
        sleep=_no_sleep,
    )
    assert outcome == "dead_lettered"
    apply.assert_not_awaited()
    record, key = dlq.sent[0]
    assert record["error"].startswith("envelope:") and record["source"] == COORDS
    assert record["raw"] == "{not json" and key is None


@pytest.mark.unit
async def test_a_transient_failure_is_retried_and_then_succeeds() -> None:
    apply = AsyncMock(side_effect=[RuntimeError("neo4j down"), RuntimeError("neo4j down"), True])
    dlq = DeadLetters()
    outcome = await projector.process_message(
        _raw(),
        coordinates=COORDS,
        apply=apply,
        dead_letters=dlq,
        max_attempts=5,
        mark_gap=MarkGap(),
        sleep=_no_sleep,
    )
    assert outcome == "applied" and apply.await_count == 3 and dlq.sent == []


@pytest.mark.unit
async def test_exhausted_retries_dead_letter_with_the_error_and_workspace_key() -> None:
    apply = AsyncMock(side_effect=RuntimeError("still down"))
    dlq = DeadLetters()
    outcome = await projector.process_message(
        _raw(),
        coordinates=COORDS,
        apply=apply,
        dead_letters=dlq,
        max_attempts=3,
        mark_gap=MarkGap(),
        sleep=_no_sleep,
    )
    assert outcome == "dead_lettered" and apply.await_count == 3
    record, key = dlq.sent[0]
    assert "still down" in record["error"] and record["attempts"] == 3 and key == WS


@pytest.mark.unit
async def test_a_deterministically_invalid_event_is_not_retried() -> None:
    apply = AsyncMock(side_effect=InvalidEventError("memory.id is not a UUID"))
    dlq = DeadLetters()
    outcome = await projector.process_message(
        _raw(),
        coordinates=COORDS,
        apply=apply,
        dead_letters=dlq,
        max_attempts=5,
        mark_gap=MarkGap(),
        sleep=_no_sleep,
    )
    assert outcome == "dead_lettered" and apply.await_count == 1


@pytest.mark.unit
async def test_if_the_dead_letter_topic_is_unwritable_the_error_propagates() -> None:
    class Broken:
        async def send(self, value, key=None) -> None:
            raise ConnectionError("broker down")

    with pytest.raises(ConnectionError):
        await projector.process_message(
            b"garbage",
            coordinates=COORDS,
            apply=AsyncMock(),
            dead_letters=Broken(),
            max_attempts=1,
            mark_gap=MarkGap(),
            sleep=_no_sleep,
        )


@pytest.mark.unit
async def test_dead_lettering_marks_the_gap_before_the_message_leaves_the_stream() -> None:
    order: list[str] = []
    gaps = MarkGap()

    async def marking(workspace_id, seq):
        order.append("mark")
        await gaps(workspace_id, seq)

    class Ordered(DeadLetters):
        async def send(self, value, key=None) -> None:
            order.append("dead-letter")
            await super().send(value, key)

    outcome = await projector.process_message(
        _raw(seq=50),
        coordinates=COORDS,
        apply=AsyncMock(side_effect=InvalidEventError("bad")),
        dead_letters=Ordered(),
        max_attempts=5,
        mark_gap=marking,
        sleep=_no_sleep,
    )
    assert outcome == "dead_lettered"
    assert gaps.marked == [(WS, 50)]
    assert order == ["mark", "dead-letter"]


@pytest.mark.unit
async def test_if_the_gap_cannot_be_marked_nothing_is_dead_lettered() -> None:
    dlq = DeadLetters()
    with pytest.raises(ConnectionError):
        await projector.process_message(
            _raw(),
            coordinates=COORDS,
            apply=AsyncMock(side_effect=InvalidEventError("bad")),
            dead_letters=dlq,
            max_attempts=1,
            mark_gap=MarkGap(fail=ConnectionError("neo4j down")),
            sleep=_no_sleep,
        )
    assert dlq.sent == [], "an unmarked gap must be redelivered, not silently dropped"


@pytest.mark.unit
async def test_a_non_uuid_workspace_id_still_reaches_the_dead_letter_topic() -> None:
    # Valid envelope shape, unusable workspace id: the real mark_gap validates the UUID and
    # raises InvalidEventError. That must not escape (it would crash-loop the projector).
    calls: list = []

    async def run(query, params):
        calls.append(query)
        return []

    async def real_mark_gap(workspace_id, seq):
        await graph_mapper.mark_gap(run, workspace_id, seq)

    raw = dumps(
        build_envelope(
            event_id=uuid.uuid4(),
            seq=9,
            occurred_at=datetime(2026, 10, 29, tzinfo=UTC),
            workspace_id="not-a-uuid",
            event_type="memory.state",
            aggregate_id=uuid.uuid4(),
            payload={"memory": {"id": str(uuid.uuid4())}},
        )
    )
    dlq = DeadLetters()
    outcome = await projector.process_message(
        raw,
        coordinates=COORDS,
        apply=AsyncMock(side_effect=InvalidEventError("workspace_id is not a UUID")),
        dead_letters=dlq,
        max_attempts=1,
        mark_gap=real_mark_gap,
        sleep=_no_sleep,
    )
    assert outcome == "dead_lettered" and len(dlq.sent) == 1
    assert calls == [], "nothing may be written to Neo4j for an unattributable message"


@pytest.mark.unit
async def test_a_real_neo4j_failure_while_marking_still_propagates() -> None:
    dlq = DeadLetters()
    with pytest.raises(ServiceUnavailable):
        await projector.process_message(
            _raw(),
            coordinates=COORDS,
            apply=AsyncMock(side_effect=InvalidEventError("bad")),
            dead_letters=dlq,
            max_attempts=1,
            mark_gap=MarkGap(fail=ServiceUnavailable("neo4j down")),
            sleep=_no_sleep,
        )
    assert dlq.sent == []


@pytest.mark.unit
async def test_an_unparseable_message_still_marks_its_workspace_when_it_names_one() -> None:
    gaps = MarkGap()
    raw = json.dumps({"workspace_id": WS, "seq": 7}).encode()  # valid JSON, not a valid envelope
    await projector.process_message(
        raw,
        coordinates=COORDS,
        apply=AsyncMock(),
        dead_letters=DeadLetters(),
        max_attempts=1,
        mark_gap=gaps,
        sleep=_no_sleep,
    )
    assert gaps.marked == [(WS, 7)]


@pytest.mark.unit
async def test_neo4j_unavailable_is_retried_past_max_attempts_never_dead_lettered() -> None:
    apply = AsyncMock(side_effect=[ServiceUnavailable("down")] * 12 + [True])
    dlq, gaps = DeadLetters(), MarkGap()
    outcome = await projector.process_message(
        _raw(),
        coordinates=COORDS,
        apply=apply,
        dead_letters=dlq,
        max_attempts=3,
        mark_gap=gaps,
        sleep=_no_sleep,
    )
    assert outcome == "applied" and apply.await_count == 13
    assert dlq.sent == [] and gaps.marked == []


@pytest.mark.unit
async def test_stopping_during_an_outage_interrupts_without_dead_lettering() -> None:
    stop = asyncio.Event()
    stop.set()
    dlq, gaps = DeadLetters(), MarkGap()
    outcome = await projector.process_message(
        _raw(),
        coordinates=COORDS,
        apply=AsyncMock(side_effect=ServiceUnavailable("down")),
        dead_letters=dlq,
        max_attempts=1,
        mark_gap=gaps,
        stop=stop,
        sleep=_no_sleep,
    )
    assert outcome == "interrupted" and dlq.sent == [] and gaps.marked == []


@pytest.mark.unit
def test_what_counts_as_transient() -> None:
    for exc in (ServiceUnavailable("x"), SessionExpired("x"), TransientError("x"), TimeoutError()):
        assert projector.is_transient(exc)
    for exc in (InvalidEventError("x"), RuntimeError("x"), ValueError("x")):
        assert not projector.is_transient(exc)


# ── relay ────────────────────────────────────────────────────────────────────


def _outbox_row(seq: int) -> dict:
    return {
        "seq": seq,
        "event_id": str(uuid.uuid4()),
        "workspace_id": WS,
        "event_type": "memory.state",
        "aggregate_id": str(uuid.uuid4()),
        "payload": {"memory": {"id": "m"}},
        "created_at": datetime(2026, 10, 29, tzinfo=UTC),
    }


class FakeConn(RoutedSession):
    pass


class Producer:
    def __init__(self, fail_on: int | None = None) -> None:
        self.sent: list[int] = []
        self._fail_on = fail_on

    async def publish(self, envelope) -> None:
        if envelope["seq"] == self._fail_on:
            raise ConnectionError("broker down")
        self.sent.append(envelope["seq"])


def _marked(conn: FakeConn) -> list[int]:
    for sql, params in conn.statements:
        if sql.startswith("UPDATE graph_outbox"):
            return params["seqs"]
    return []


@pytest.mark.unit
async def test_relay_publishes_in_seq_order_and_marks_exactly_those_rows() -> None:
    conn = FakeConn({"FROM graph_outbox": Rows([_outbox_row(1), _outbox_row(2), _outbox_row(3)])})
    producer = Producer()
    sent = await relay.publish_pending(conn, producer, batch_size=10, settle_seconds=2.0)
    assert sent == 3 and producer.sent == [1, 2, 3] and _marked(conn) == [1, 2, 3]
    assert conn.commits == 1


@pytest.mark.unit
async def test_relay_stops_at_the_first_failure_and_still_marks_what_was_acknowledged() -> None:
    conn = FakeConn({"FROM graph_outbox": Rows([_outbox_row(1), _outbox_row(2), _outbox_row(3)])})
    producer = Producer(fail_on=2)
    with pytest.raises(ConnectionError):
        await relay.publish_pending(conn, producer, batch_size=10, settle_seconds=2.0)
    assert producer.sent == [1]  # 3 was NOT sent past the failure: order is never skipped
    assert _marked(conn) == [1]  # row 2 and 3 stay unpublished and are retried
    assert conn.commits == 1


@pytest.mark.unit
async def test_relay_with_nothing_pending_marks_nothing() -> None:
    conn = FakeConn({"FROM graph_outbox": Rows([])})
    assert await relay.publish_pending(conn, Producer(), batch_size=10, settle_seconds=2.0) == 0
    assert _marked(conn) == []


@pytest.mark.unit
async def test_relay_query_orders_by_seq_and_honours_the_settle_window() -> None:
    conn = FakeConn({"FROM graph_outbox": Rows([])})
    await relay.publish_pending(conn, Producer(), batch_size=7, settle_seconds=2.5)
    sql, params = conn.statements[0]
    assert "ORDER BY seq" in sql and "published_at IS NULL" in sql and "make_interval" in sql
    assert params == {"settle": 2.5, "limit": 7}


@pytest.mark.unit
def test_envelope_is_built_from_the_outbox_row_without_adding_fields() -> None:
    row = relay.OutboxRow(**_outbox_row(9))
    envelope = relay.to_envelope(row)
    assert envelope["seq"] == 9 and envelope["workspace_id"] == WS
    assert set(json.loads(dumps(envelope))) == {
        "schema_version",
        "event_id",
        "seq",
        "occurred_at",
        "workspace_id",
        "type",
        "aggregate_id",
        "payload",
    }


@pytest.mark.unit
async def test_the_relay_refuses_to_start_without_its_database_url(monkeypatch) -> None:
    from types import SimpleNamespace

    with patch.object(
        relay, "get_settings", return_value=SimpleNamespace(graph_relay_database_url="")
    ):
        assert await relay.run() == 2


@pytest.mark.unit
async def test_a_second_relay_does_not_acquire_the_advisory_lock() -> None:
    conn = FakeConn({"pg_try_advisory_lock": Rows(scalar=False)})
    assert await relay.acquire_singleton(conn) is False
    conn = FakeConn({"pg_try_advisory_lock": Rows(scalar=True)})
    assert await relay.acquire_singleton(conn) is True


# ── backfill ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_backfill_walks_the_workspace_in_id_order_and_commits_per_batch() -> None:
    ids = [str(uuid.uuid4()) for _ in range(5)]
    pages = iter(
        [
            Rows([{"id": i} for i in ids[:2]]),
            Rows([{"id": i} for i in ids[2:4]]),
            Rows([{"id": ids[4]}]),
            Rows([]),
        ]
    )

    class Paged(RoutedSession):
        async def execute(self, statement, params=None):
            self.statements.append((" ".join(str(statement).split()), params or {}))
            return next(pages)

    session = Paged()
    emitted: list[list[str]] = []

    async def fake_emit(_session, memory_ids, *, reason, force):
        assert reason == "backfill" and force is True
        emitted.append(list(memory_ids))
        return len(memory_ids)

    with patch.object(backfill, "emit_memory_states", fake_emit):
        total = await backfill.backfill_workspace(session, uuid.uuid4(), batch_size=2)

    assert total == 5 and [len(batch) for batch in emitted] == [2, 2, 1]
    assert session.commits == 3
    # Keyset pagination: later pages resume after the previous page's last id.
    assert "id > CAST(:last AS uuid)" in session.statements[1][0]
    assert session.statements[1][1]["last"] == ids[1]


@pytest.mark.unit
async def test_find_owner_returns_none_when_row_level_security_hides_everyone() -> None:
    assert (
        await backfill.find_owner(RoutedSession({"workspace_members": Rows([])}), uuid.uuid4())
        is None
    )
    owner = str(uuid.uuid4())
    found = await backfill.find_owner(
        RoutedSession({"workspace_members": Rows([{"user_id": owner}])}), uuid.uuid4()
    )
    assert str(found) == owner
