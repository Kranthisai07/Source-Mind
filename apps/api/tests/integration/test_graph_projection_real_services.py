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
        get_settings.cache_clear()


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
