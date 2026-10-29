"""The graph projection against a real Neo4j and a real Redpanda.

Needs the disposable stack from ``infra/docker-compose.graph-test.yml``. Configure with:

* ``GRAPH_TEST_NEO4J_PASSWORD``: the password that stack was started with;
* optional ``GRAPH_TEST_NEO4J_URI`` (default ``bolt://127.0.0.1:57687``) and
  ``GRAPH_TEST_KAFKA`` (default ``127.0.0.1:59092``).

The tests refuse anything that is not on loopback at those fixed ports, and wipe the Neo4j
database they connect to, so never point them at anything but the disposable stack.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from aiokafka import AIOKafkaConsumer

from sourcemind.core import graph
from sourcemind.core.config import get_settings
from sourcemind.core.graph import ensure_schema, get_graph_session, run_read
from sourcemind.events import projector
from sourcemind.events.envelope import build_envelope, parse_envelope
from sourcemind.events.producer import GraphEventProducer, ensure_topics

pytestmark = pytest.mark.asyncio

NEO4J_URI = os.environ.get("GRAPH_TEST_NEO4J_URI", "bolt://127.0.0.1:57687")
KAFKA = os.environ.get("GRAPH_TEST_KAFKA", "127.0.0.1:59092")


def _password() -> str:
    password = os.environ.get("GRAPH_TEST_NEO4J_PASSWORD")
    if not password:
        pytest.skip(
            "GRAPH_TEST_NEO4J_PASSWORD is not set (see infra/docker-compose.graph-test.yml)"
        )
    if NEO4J_URI != "bolt://127.0.0.1:57687" or KAFKA != "127.0.0.1:59092":
        raise RuntimeError("refusing a graph test stack that is not the disposable loopback one")
    return password


@pytest_asyncio.fixture
async def stack(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, str]]:
    password = _password()
    suffix = uuid.uuid4().hex[:8]
    names = {
        "topic": f"graph.test.{suffix}",
        "dlq": f"graph.test.{suffix}.dlq",
        "group": f"graph-test-{suffix}",
    }
    for key, value in {
        "AUTH_DEV_BYPASS_ENABLED": "true",
        "NEO4J_URI": NEO4J_URI,
        "NEO4J_AUTH": f"neo4j/{password}",
        "KAFKA_BOOTSTRAP_SERVERS": KAFKA,
        "KAFKA_GRAPH_TOPIC": names["topic"],
        "KAFKA_GRAPH_DLQ_TOPIC": names["dlq"],
        "KAFKA_CONSUMER_GROUP": names["group"],
        "GRAPH_PROJECTOR_MAX_ATTEMPTS": "2",
        # A 512 MB Redpanda allows ~128 partitions in total and refuses to create more, so these
        # topics are single-partition and are deleted afterwards (see _delete_topics).
        "KAFKA_GRAPH_PARTITIONS": "1",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    await graph.init_graph()
    try:
        await ensure_schema()
        async with get_graph_session() as session:
            await session.run("MATCH (n) DETACH DELETE n")
        yield names
    finally:
        await graph.close_graph()
        await _delete_topics(KAFKA, [names["topic"], names["dlq"]])
        get_settings.cache_clear()


async def _delete_topics(bootstrap: str, topics: list[str]) -> None:
    """Best effort: leaving topics behind eventually exhausts the broker's partition budget."""
    from aiokafka.admin import AIOKafkaAdminClient

    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap)
    try:
        await admin.start()
        await admin.delete_topics(topics)
    except Exception:  # noqa: BLE001  (cleanup must never fail a test)
        pass
    finally:
        await admin.close()


# ── helpers ──────────────────────────────────────────────────────────────────


def _event(ws: str, mem: str, seq: int, **payload):
    memory = {
        "id": mem,
        "version": 1,
        "parent_id": None,
        "current": True,
        "deleted": False,
        "deleted_reason": None,
        "stale": False,
        "importance": 0.5,
    }
    memory.update(payload.pop("memory", {}))
    body = {"memory": memory, "contributors": [], "relations_out": [], "relations_in": []}
    body.update(payload)
    envelope = build_envelope(
        event_id=uuid.uuid4(),
        seq=seq,
        occurred_at=datetime(2026, 10, 29, tzinfo=UTC),
        workspace_id=ws,
        event_type="memory.state",
        aggregate_id=mem,
        payload=body,
    )
    return envelope


def _graph_event(*args, **kwargs):
    return parse_envelope(json.dumps(_event(*args, **kwargs)))


async def _count(ws: str, cypher: str, **params) -> int:
    rows = await run_read(cypher, ws, params)
    return rows[0]["n"]


MEMORY_BY_KEY = "MATCH (m:Memory {key: $key, workspace_id: $workspace_id}) RETURN m AS m"


async def _memory(ws: str, mem: str) -> dict:
    rows = await run_read(MEMORY_BY_KEY, ws, {"key": f"{ws}:{mem}"})
    return dict(rows[0]["m"]) if rows else {}


# ── Cypher against a real Neo4j ──────────────────────────────────────────────


async def test_schema_creation_is_idempotent(stack) -> None:
    await ensure_schema()
    await ensure_schema()
    async with get_graph_session() as session:
        result = await session.run("SHOW CONSTRAINTS YIELD name RETURN collect(name) AS names")
        names = (await result.single())["names"]
    assert {"workspace_key", "user_key", "memory_key"} <= set(names)


async def test_a_full_event_builds_the_expected_graph(stack) -> None:
    ws, mem, parent, target, source, user = (str(uuid.uuid4()) for _ in range(6))
    event = _graph_event(
        ws,
        mem,
        10,
        memory={"version": 2, "parent_id": parent, "importance": 0.9},
        contributors=[
            {"user_id": user, "weight": 0.75, "action": "edit", "edit_id": str(uuid.uuid4())}
        ],
        relations_out=[
            {"id": str(uuid.uuid4()), "target": target, "type": "extends", "confidence": 0.9}
        ],
        relations_in=[
            {"id": str(uuid.uuid4()), "source": source, "type": "updates", "confidence": None}
        ],
    )
    assert await projector.apply_to_neo4j(event) is True

    node = await _memory(ws, mem)
    assert node["version"] == 2 and node["current"] is True and node["importance"] == 0.9
    assert node["parent_id"] == parent and node["seq"] == 10

    assert (
        await _count(
            ws,
            "MATCH (:Memory {key: $m, workspace_id: $workspace_id})-[:PARENT]->"
            "(p:Memory {key: $p}) RETURN count(p) AS n",
            m=f"{ws}:{mem}",
            p=f"{ws}:{parent}",
        )
        == 1
    )
    rows = await run_read(
        "MATCH (u:User)-[c:CONTRIBUTED {workspace_id: $workspace_id}]->(m:Memory {key: $m}) "
        "RETURN u.id AS user, c.weight AS weight, c.action AS action",
        ws,
        {"m": f"{ws}:{mem}"},
    )
    assert rows == [{"user": user, "weight": 0.75, "action": "edit"}]
    assert (
        await _count(
            ws,
            "MATCH (:Memory {key: $m, workspace_id: $workspace_id})-[r:RELATES]->"
            "(t:Memory {key: $t}) RETURN count(r) AS n",
            m=f"{ws}:{mem}",
            t=f"{ws}:{target}",
        )
        == 1
    )
    assert (
        await _count(
            ws,
            "MATCH (s:Memory {key: $s})-[r:RELATES]->(:Memory {key: $m, "
            "workspace_id: $workspace_id}) RETURN count(r) AS n",
            s=f"{ws}:{source}",
            m=f"{ws}:{mem}",
        )
        == 1
    )
    watermark = await run_read(
        "MATCH (w:Workspace {key: $workspace_id}) RETURN w.applied_seq AS n", ws
    )
    assert watermark[0]["n"] == 10


async def test_duplicates_and_stale_events_change_nothing(stack) -> None:
    ws, mem, user = (str(uuid.uuid4()) for _ in range(3))
    new = _graph_event(
        ws,
        mem,
        20,
        memory={"version": 3},
        contributors=[{"user_id": user, "weight": 0.9, "action": "edit", "edit_id": None}],
    )
    old = _graph_event(
        ws,
        mem,
        5,
        memory={"version": 1},
        contributors=[{"user_id": user, "weight": 0.1, "action": "create", "edit_id": None}],
    )

    assert await projector.apply_to_neo4j(new) is True
    assert await projector.apply_to_neo4j(new) is False  # exact duplicate
    assert await projector.apply_to_neo4j(old) is False  # arrives late, older seq

    node = await _memory(ws, mem)
    assert node["version"] == 3 and node["seq"] == 20
    rows = await run_read(
        "MATCH (:User)-[c:CONTRIBUTED]->(:Memory {key: $m, workspace_id: "
        "$workspace_id}) RETURN c.weight AS w",
        ws,
        {"m": f"{ws}:{mem}"},
    )
    assert rows == [{"w": 0.9}]


async def test_newer_state_replaces_contributions_and_relations_wholesale(stack) -> None:
    ws, mem, t1, t2, u1, u2 = (str(uuid.uuid4()) for _ in range(6))
    first = _graph_event(
        ws,
        mem,
        1,
        contributors=[
            {"user_id": u1, "weight": 0.6, "action": "create", "edit_id": None},
            {"user_id": u2, "weight": 0.4, "action": "edit", "edit_id": None},
        ],
        relations_out=[
            {"id": str(uuid.uuid4()), "target": t1, "type": "extends", "confidence": 1.0},
            {"id": str(uuid.uuid4()), "target": t2, "type": "updates", "confidence": 1.0},
        ],
    )
    second = _graph_event(
        ws,
        mem,
        2,
        contributors=[{"user_id": u1, "weight": 1.0, "action": "edit", "edit_id": None}],
        relations_out=[],
    )
    await projector.apply_to_neo4j(first)
    await projector.apply_to_neo4j(second)
    key = f"{ws}:{mem}"
    assert (
        await _count(
            ws,
            "MATCH (:User)-[c:CONTRIBUTED]->(:Memory {key: $m, workspace_id: "
            "$workspace_id}) RETURN count(c) AS n",
            m=key,
        )
        == 1
    )
    assert (
        await _count(
            ws,
            "MATCH (:Memory {key: $m, workspace_id: $workspace_id})-[r:RELATES]->() "
            "RETURN count(r) AS n",
            m=key,
        )
        == 0
    )


async def test_a_deleted_memory_is_flagged_and_keeps_its_contributions(stack) -> None:
    ws, mem, user = (str(uuid.uuid4()) for _ in range(3))
    await projector.apply_to_neo4j(
        _graph_event(
            ws,
            mem,
            1,
            contributors=[{"user_id": user, "weight": 1.0, "action": "create", "edit_id": None}],
        )
    )
    await projector.apply_to_neo4j(
        _graph_event(
            ws,
            mem,
            2,
            memory={"current": False, "deleted": True, "deleted_reason": "user_deleted"},
            contributors=[{"user_id": user, "weight": 1.0, "action": "create", "edit_id": None}],
        )
    )
    node = await _memory(ws, mem)
    assert node["deleted"] is True and node["deleted_reason"] == "user_deleted"
    assert (
        await _count(
            ws,
            "MATCH (:User)-[c:CONTRIBUTED]->(:Memory {key: $m, workspace_id: "
            "$workspace_id}) RETURN count(c) AS n",
            m=f"{ws}:{mem}",
        )
        == 1
    )


async def test_the_same_ids_in_two_workspaces_stay_separate(stack) -> None:
    ws_a, ws_b, mem, user = (str(uuid.uuid4()) for _ in range(4))
    for ws, weight in ((ws_a, 0.2), (ws_b, 0.8)):
        await projector.apply_to_neo4j(
            _graph_event(
                ws,
                mem,
                1,
                contributors=[
                    {"user_id": user, "weight": weight, "action": "edit", "edit_id": None}
                ],
            )
        )
    for ws, weight in ((ws_a, 0.2), (ws_b, 0.8)):
        rows = await run_read(
            "MATCH (:User)-[c:CONTRIBUTED {workspace_id: $workspace_id}]->(m:Memory) "
            "RETURN m.key AS key, c.weight AS w",
            ws,
        )
        assert rows == [{"key": f"{ws}:{mem}", "w": weight}]
    # Looked at without the tenant filter (a raw session, deliberately): two distinct nodes exist.
    async with get_graph_session() as session:
        result = await session.run("MATCH (m:Memory {id: $m}) RETURN count(m) AS n", m=mem)
        assert (await result.single())["n"] == 2


async def test_an_edge_to_a_memory_in_another_workspace_cannot_be_created(stack) -> None:
    # Keys embed the event's workspace, so a relation can only ever point inside it.
    ws, other, mem, target = (str(uuid.uuid4()) for _ in range(4))
    await projector.apply_to_neo4j(
        _graph_event(
            ws,
            mem,
            1,
            relations_out=[
                {"id": str(uuid.uuid4()), "target": target, "type": "extends", "confidence": 1}
            ],
        )
    )
    rows = await run_read(
        "MATCH (:Memory {key: $m, workspace_id: $workspace_id})-[:RELATES]->(t:Memory) "
        "RETURN t.workspace_id AS w",
        ws,
        {"m": f"{ws}:{mem}"},
    )
    assert rows == [{"w": ws}]
    assert (
        await _count(other, "MATCH (n:Memory {workspace_id: $workspace_id}) RETURN count(n) AS n")
        == 0
    )


# ── relay publisher -> Redpanda -> projector -> Neo4j ────────────────────────


async def _wait_for(predicate, *, timeout: float = 45.0, what: str = "condition") -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            if await predicate():
                return
        except RuntimeError:
            pass  # the projector has not opened its Neo4j driver yet
        await asyncio.sleep(0.5)
    raise AssertionError(f"timed out waiting for {what}")


async def _run_projector_until(stop: asyncio.Event, check, what: str) -> None:
    # The projector owns its Neo4j driver (it is a separate process in production), so hand the
    # shared one over and take it back afterwards for the assertions.
    await graph.close_graph()
    task = asyncio.create_task(projector.run(stop))
    try:
        await _wait_for(check, what=what)
    finally:
        stop.set()
        assert await asyncio.wait_for(task, timeout=30) == 0
        await graph.init_graph()


async def test_events_flow_from_the_producer_through_redpanda_into_neo4j(stack) -> None:
    ws, mem, user = (str(uuid.uuid4()) for _ in range(3))
    await ensure_topics()
    async with GraphEventProducer() as producer:
        # Published out of order: the newer state must win whatever the arrival order.
        await producer.publish(
            _event(
                ws,
                mem,
                7,
                memory={"version": 3},
                contributors=[{"user_id": user, "weight": 0.9, "action": "edit", "edit_id": None}],
            )
        )
        await producer.publish(
            _event(
                ws,
                mem,
                3,
                memory={"version": 1},
                contributors=[
                    {"user_id": user, "weight": 0.1, "action": "create", "edit_id": None}
                ],
            )
        )

    async def applied() -> bool:
        node = await _memory(ws, mem)
        return node.get("seq") == 7

    await _run_projector_until(asyncio.Event(), applied, "the newest event to be applied")
    node = await _memory(ws, mem)
    assert node["version"] == 3
    rows = await run_read(
        "MATCH (:User)-[c:CONTRIBUTED]->(:Memory {key: $m, workspace_id: "
        "$workspace_id}) RETURN c.weight AS w",
        ws,
        {"m": f"{ws}:{mem}"},
    )
    assert rows == [{"w": 0.9}]


async def test_a_poison_message_goes_to_the_dead_letter_topic_and_does_not_block_the_stream(
    stack,
) -> None:
    ws, mem = str(uuid.uuid4()), str(uuid.uuid4())
    await ensure_topics()
    async with GraphEventProducer() as producer:
        await producer.send({"not": "an envelope"}, key=None)
        bad = _event(ws, str(uuid.uuid4()), 1)
        bad["payload"]["memory"]["id"] = "definitely-not-a-uuid"
        await producer.publish(bad)
        await producer.publish(_event(ws, mem, 2))  # a good event BEHIND the poison ones

    async def good_applied() -> bool:
        return (await _memory(ws, mem)).get("seq") == 2

    await _run_projector_until(asyncio.Event(), good_applied, "the good event behind two bad ones")

    consumer = AIOKafkaConsumer(
        stack["dlq"],
        bootstrap_servers=KAFKA,
        group_id=f"{stack['group']}-dlq-reader",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await consumer.start()
    try:
        records = []
        deadline = asyncio.get_running_loop().time() + 20
        while len(records) < 2 and asyncio.get_running_loop().time() < deadline:
            for batch in (await consumer.getmany(timeout_ms=1000)).values():
                records.extend(json.loads(m.value) for m in batch)
    finally:
        await consumer.stop()
    errors = sorted(r["error"].split(":")[0] for r in records)
    assert errors == ["envelope", "invalid"]
    assert all(r["source"]["topic"] == stack["topic"] for r in records)


# ── dead-letter gaps and backfill markers (D-062) ────────────────────────────────

WATERMARK_AND_GAP = (
    "MATCH (w:Workspace {key: $workspace_id}) "
    "RETURN w.applied_seq AS applied, w.gap_seq AS gap"
)


class _Dlq:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, value, key=None) -> None:
        self.sent.append(value)


async def _process(envelope: dict, dlq: _Dlq) -> str:
    return await projector.process_message(
        json.dumps(envelope).encode(),
        coordinates={"topic": "t", "partition": 0, "offset": 0},
        apply=projector.apply_to_neo4j,
        dead_letters=dlq,
        max_attempts=2,
        mark_gap=projector.mark_gap_in_neo4j,
    )


def _marker(ws: str, seq: int, kind: str, payload: dict | None = None) -> dict:
    return build_envelope(
        event_id=uuid.uuid4(),
        seq=seq,
        occurred_at=datetime(2026, 10, 29, tzinfo=UTC),
        workspace_id=ws,
        event_type=kind,
        aggregate_id=ws,
        payload=payload or {},
    )


async def _state(ws: str) -> dict:
    rows = await run_read(WATERMARK_AND_GAP, ws)
    return rows[0] if rows else {}


async def _fresh(ws: str, published: int) -> bool:
    from sourcemind.services.graph import reads
    from tests.unit.events.fakes import RoutedSession, Rows

    session = RoutedSession({"FROM graph_publication_cursor": Rows([{"published_seq": published}])})
    return await reads._graph_fresh(session, uuid.UUID(ws))


async def test_a_later_event_cannot_hide_a_dead_lettered_one(stack) -> None:
    ws, lost, fine = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    dlq = _Dlq()
    bad = _event(ws, lost, 50)
    bad["payload"]["memory"]["id"] = "not-a-uuid"
    assert await _process(bad, dlq) == "dead_lettered"
    assert await _process(_event(ws, fine, 60), dlq) == "applied"

    state = await _state(ws)
    # The watermark is past the lost event, which is exactly how it used to look fresh...
    assert state["applied"] == 60 and state["gap"] == 50
    # ...and now it is not trusted: the API must answer from PostgreSQL.
    assert await _fresh(ws, published=60) is False


async def test_a_completed_backfill_repairs_the_gap_and_freshness_returns(stack) -> None:
    ws, lost, other = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    dlq = _Dlq()
    bad = _event(ws, lost, 50)
    bad["payload"]["memory"]["id"] = "not-a-uuid"
    await _process(bad, dlq)
    await _process(_event(ws, other, 60), dlq)
    assert (await _state(ws))["gap"] == 50

    await _process(_marker(ws, 70, "workspace.backfill_started"), dlq)
    await _process(_event(ws, lost, 71), dlq)  # the backfill re-emits the lost memory, now valid
    await _process(
        _marker(ws, 72, "workspace.backfill_complete", {"started_seq": 70, "emitted": 2}), dlq
    )

    state = await _state(ws)
    assert state["gap"] is None and state["applied"] == 72
    assert (await _memory(ws, lost)).get("seq") == 71
    assert await _fresh(ws, published=72) is True


async def test_an_incomplete_or_failed_backfill_leaves_the_workspace_on_postgres(stack) -> None:
    ws, other = str(uuid.uuid4()), str(uuid.uuid4())
    dlq = _Dlq()
    bad = _event(ws, str(uuid.uuid4()), 50)
    bad["payload"]["memory"]["id"] = "not-a-uuid"
    await _process(bad, dlq)
    await _process(_marker(ws, 70, "workspace.backfill_started"), dlq)
    # No completion marker (the backfill died): still gapped.
    await _process(_event(ws, other, 71), dlq)
    assert (await _state(ws))["gap"] == 50 and await _fresh(ws, published=71) is False

    # A backfill event that itself dead-letters records a NEWER gap than the run's start, so the
    # completion marker must not clear it.
    worse = _event(ws, str(uuid.uuid4()), 72)
    worse["payload"]["memory"]["id"] = "not-a-uuid"
    await _process(worse, dlq)
    await _process(_marker(ws, 73, "workspace.backfill_complete", {"started_seq": 70}), dlq)
    state = await _state(ws)
    assert state["gap"] == 72
    assert await _fresh(ws, published=73) is False


async def test_a_gap_in_one_workspace_does_not_affect_another(stack) -> None:
    ws_a, ws_b = str(uuid.uuid4()), str(uuid.uuid4())
    dlq = _Dlq()
    bad = _event(ws_a, str(uuid.uuid4()), 5)
    bad["payload"]["memory"]["id"] = "not-a-uuid"
    await _process(bad, dlq)
    await _process(_event(ws_b, str(uuid.uuid4()), 6), dlq)
    assert (await _state(ws_a))["gap"] == 5
    assert (await _state(ws_b)).get("gap") is None
    assert await _fresh(ws_b, published=6) is True


async def test_neo4j_enforces_the_query_timeout_instead_of_stalling(
    stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    import neo4j.exceptions

    from sourcemind.core.graph import GraphQueryError

    # `$workspace_id` is in the text only to pass the tenancy guard; the work is the loop.
    slow = (
        "UNWIND range(1, 2000000000) AS x WITH x WHERE x < 0 AND $workspace_id IS NOT NULL "
        "RETURN count(x) AS n"
    )
    monkeypatch.setenv("NEO4J_QUERY_TIMEOUT_SECONDS", "0.3")
    # Push the client-side deadline far out so only Neo4j's own transaction timeout can end this
    # query. Otherwise the outer deadline would pass the test even with the original bug.
    monkeypatch.setattr(graph, "_TIMEOUT_GRACE_SECONDS", 60)
    get_settings.cache_clear()
    try:
        started = time.monotonic()
        with pytest.raises(neo4j.exceptions.Neo4jError) as raised:
            await run_read(slow, str(uuid.uuid4()))
        elapsed = time.monotonic() - started
        assert GraphQueryError not in type(raised.value).__mro__  # the server stopped it
    finally:
        monkeypatch.delenv("NEO4J_QUERY_TIMEOUT_SECONDS")
        get_settings.cache_clear()
    assert elapsed < 5, f"the query ran for {elapsed:.1f}s: the timeout is not being applied"


async def test_an_event_with_a_non_uuid_workspace_is_dead_lettered_not_fatal(stack) -> None:
    """The projector must survive a message whose workspace cannot be attributed (Codex, #45)."""
    dlq = _Dlq()
    odd = _event("not-a-uuid", str(uuid.uuid4()), 5)
    assert await _process(odd, dlq) == "dead_lettered"
    assert len(dlq.sent) == 1
    # And the next good event is still applied.
    ws, mem = str(uuid.uuid4()), str(uuid.uuid4())
    assert await _process(_event(ws, mem, 6), dlq) == "applied"
