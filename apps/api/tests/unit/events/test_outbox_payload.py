"""The outbox writer: what an event contains, and what it must never contain."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from sourcemind.services.graph.outbox import (
    emit_memory_states,
    emit_workspace_marker,
    load_memory_states,
)
from tests.unit.events.fakes import RoutedSession, Rows

WS = str(uuid4())
M1, M2, M3 = (str(uuid4()) for _ in range(3))
USER_A, USER_B = str(uuid4()), str(uuid4())


def _flag(on: bool):
    return patch(
        "sourcemind.services.graph.outbox.get_settings",
        return_value=SimpleNamespace(ff_kafka_events=on),
    )


def _memory(mid: str, **overrides):
    row = {
        "id": mid,
        "workspace_id": WS,
        "version": 1,
        "parent_id": None,
        "current_version": True,
        "deleted": False,
        "deleted_reason": None,
        "stale": False,
        "importance": 0.4,
    }
    return {**row, **overrides}


def _session(memories, contributors=(), relations=(), neighbours=()):
    memories = list(memories)
    return RoutedSession(
        {
            # Like the real query: only the requested ids come back.
            "FROM memories": lambda params: Rows([m for m in memories if m["id"] in params["ids"]]),
            "FROM attributions": Rows(list(contributors)),
            "SELECT id::text AS id, source_memory_id": Rows(list(relations)),
            "UNION": Rows([{"id": n} for n in neighbours]),
        }
    )


@pytest.mark.unit
async def test_flag_off_writes_nothing_and_does_not_even_read() -> None:
    session = _session([_memory(M1)])
    with _flag(False):
        assert await emit_memory_states(session, [M1], reason="edit") == 0
    assert session.statements == []


@pytest.mark.unit
async def test_force_bypasses_the_flag_for_the_backfill() -> None:
    session = _session([_memory(M1)])
    with _flag(False):
        assert await emit_memory_states(session, [M1], reason="backfill", force=True) == 1
    assert len(session.inserts()) == 1


@pytest.mark.unit
async def test_one_event_per_memory_carries_state_and_workspace() -> None:
    session = _session(
        [_memory(M1, version=2, parent_id=M2, importance=0.9), _memory(M2, current_version=False)],
        contributors=[
            {"memory_id": M1, "user_id": USER_A, "weight": 0.7, "action": "edit", "edit_id": None},
            {
                "memory_id": M1,
                "user_id": USER_B,
                "weight": 0.3,
                "action": "transfer",
                "edit_id": None,
            },
        ],
    )
    with _flag(True):
        assert await emit_memory_states(session, [M1, M2], reason="edit") == 2

    by_memory = {p["agg"]: json.loads(p["payload"]) for p in session.inserts()}
    first = by_memory[M1]
    assert first["memory"] == {
        "id": M1,
        "version": 2,
        "parent_id": M2,
        "current": True,
        "deleted": False,
        "deleted_reason": None,
        "stale": False,
        "importance": 0.9,
    }
    assert {c["user_id"]: c["weight"] for c in first["contributors"]} == {USER_A: 0.7, USER_B: 0.3}
    assert by_memory[M2]["memory"]["current"] is False
    assert all(p["ws"] == WS and p["type"] == "memory.state" for p in session.inserts())


@pytest.mark.unit
async def test_weights_are_passed_through_not_normalised() -> None:
    session = _session(
        [_memory(M1)],
        contributors=[
            {
                "memory_id": M1,
                "user_id": USER_A,
                "weight": 0.9,
                "action": "create",
                "edit_id": None,
            },
            {
                "memory_id": M1,
                "user_id": USER_B,
                "weight": 0.36,
                "action": "transfer",
                "edit_id": None,
            },
        ],
    )
    (state,) = await load_memory_states(session, [M1], "handoff_assign")
    assert sum(c["weight"] for c in state["payload"]["contributors"]) == pytest.approx(1.26)


@pytest.mark.unit
async def test_payload_never_carries_content_or_personal_data() -> None:
    session = _session(
        [_memory(M1)],
        contributors=[
            {"memory_id": M1, "user_id": USER_A, "weight": 1.0, "action": "create", "edit_id": None}
        ],
    )
    with _flag(True):
        await emit_memory_states(session, [M1], reason="edit")
    flat = json.dumps(session.inserts()[0]["payload"])
    for forbidden in ("content", "embedding", "email", "display_name"):
        assert forbidden not in flat
    sql_text = " ".join(sql for sql, _ in session.statements)
    assert "content," not in sql_text and "embedding" not in sql_text
    assert "email" not in sql_text and "display_name" not in sql_text


@pytest.mark.unit
async def test_outgoing_relations_are_authoritative_and_incoming_are_hints() -> None:
    rel_out = {"id": str(uuid4()), "source": M1, "target": M2, "type": "extends", "confidence": 0.9}
    rel_in = {"id": str(uuid4()), "source": M3, "target": M1, "type": "updates", "confidence": None}
    rel_internal = {
        "id": str(uuid4()),
        "source": M2,
        "target": M1,
        "type": "derives",
        "confidence": 0.8,
    }
    session = _session([_memory(M1), _memory(M2)], relations=[rel_out, rel_in, rel_internal])
    states = {
        s["payload"]["memory"]["id"]: s["payload"]
        for s in await load_memory_states(session, [M1, M2], "ingest")
    }

    assert [r["target"] for r in states[M1]["relations_out"]] == [M2]
    # M3 is outside this batch, so its edge is a hint.
    # M2's own edge to M1 is not: M2 is emitted in the same batch.
    assert [r["source"] for r in states[M1]["relations_in"]] == [M3]
    assert states[M1]["relations_in"][0]["confidence"] is None
    assert [r["target"] for r in states[M2]["relations_out"]] == [M1]


@pytest.mark.unit
async def test_neighbours_are_added_only_when_asked() -> None:
    session = _session([_memory(M1), _memory(M3)], neighbours=[M3])
    with _flag(True):
        assert await emit_memory_states(session, [M1], reason="ingest") == 1
        session.statements.clear()
        assert (
            await emit_memory_states(session, [M1], reason="ingest", include_neighbours=True) == 2
        )


@pytest.mark.unit
async def test_ids_are_deduplicated_and_invisible_memories_skipped() -> None:
    session = _session([_memory(M1)])  # M2 is hidden by row-level security
    with _flag(True):
        written = await emit_memory_states(session, [M1, M1, M2], reason="edit")
    assert written == 1
    assert len(session.inserts()) == 1


@pytest.mark.unit
async def test_no_ids_means_no_queries() -> None:
    session = _session([])
    with _flag(True):
        assert await emit_memory_states(session, [], reason="edit") == 0
    assert session.statements == []


@pytest.mark.unit
async def test_insert_does_not_use_returning_because_the_runtime_role_cannot_select() -> None:
    session = _session([_memory(M1)])
    with _flag(True):
        await emit_memory_states(session, [M1], reason="edit")
    insert_sql = next(sql for sql, _ in session.statements if sql.startswith("INSERT"))
    assert "RETURNING" not in insert_sql.upper()


def _locks(session):
    return [
        (sql.split("(")[0].replace("SELECT ", ""), params["key"])
        for sql, params in session.statements
        if "pg_advisory_xact_lock" in sql
    ]


@pytest.mark.unit
async def test_a_normal_emit_takes_the_workspace_lock_shared_before_reading_state() -> None:
    session = _session([_memory(M1)])
    with _flag(True):
        await emit_memory_states(session, [M1], reason="edit")
    assert _locks(session) == [("pg_advisory_xact_lock_shared", f"graph_emit:{WS}")]
    order = [sql for sql, _ in session.statements]
    lock_at = next(i for i, sql in enumerate(order) if "pg_advisory_xact_lock" in sql)
    state_at = next(i for i, sql in enumerate(order) if "FROM attributions" in sql)
    assert lock_at < state_at, "state must be read only after the lock is held"


@pytest.mark.unit
async def test_the_backfill_takes_it_exclusive() -> None:
    session = _session([_memory(M1)])
    with _flag(False):
        await emit_memory_states(session, [M1], reason="backfill", force=True)
    assert _locks(session) == [("pg_advisory_xact_lock", f"graph_emit:{WS}")]


@pytest.mark.unit
async def test_workspaces_are_locked_once_each_in_sorted_order() -> None:
    other = str(uuid4())
    session = _session([_memory(M1), _memory(M2, workspace_id=other), _memory(M3)])
    with _flag(True):
        await emit_memory_states(session, [M1, M2, M3], reason="edit")
    keys = [key for _, key in _locks(session)]
    assert keys == sorted(f"graph_emit:{w}" for w in {WS, other})


@pytest.mark.unit
async def test_flag_off_takes_no_lock_and_unseen_memories_lock_nothing() -> None:
    session = _session([_memory(M1)])
    with _flag(False):
        await emit_memory_states(session, [M1], reason="edit")
    assert session.statements == []
    session = _session([])
    with _flag(True):
        assert await emit_memory_states(session, [M1], reason="edit") == 0
    assert _locks(session) == []


@pytest.mark.unit
async def test_a_workspace_marker_is_inserted_and_its_seq_comes_from_currval() -> None:
    session = RoutedSession({"currval": Rows(scalar=42)})
    seq = await emit_workspace_marker(session, WS, "workspace.backfill_started", {"x": 1})
    assert seq == 42
    (insert,) = session.inserts()
    assert insert["ws"] == WS and insert["type"] == "workspace.backfill_started"
    assert json.loads(insert["payload"]) == {"x": 1}
    # It never reads the outbox (the runtime role may not): only the sequence it just used.
    assert not any("FROM graph_outbox" in sql for sql, _ in session.statements)
