"""Event -> Cypher: tenancy, idempotency, and that values never become query text."""

from __future__ import annotations

from uuid import uuid4

import pytest

from sourcemind.events import graph_mapper as gm
from sourcemind.events.envelope import GraphEvent

WS = str(uuid4())
OTHER_WS = str(uuid4())
MEM, PARENT, TARGET, SOURCE, USER = (str(uuid4()) for _ in range(5))


def _event(payload=None, *, seq=10, workspace_id=WS, type_="memory.state") -> GraphEvent:
    base = {
        "memory": {
            "id": MEM,
            "version": 2,
            "parent_id": PARENT,
            "current": True,
            "deleted": False,
            "deleted_reason": None,
            "stale": False,
            "importance": 0.5,
        },
        "contributors": [
            {"user_id": USER, "weight": 0.6, "action": "edit", "edit_id": str(uuid4())}
        ],
        "relations_out": [
            {"id": str(uuid4()), "target": TARGET, "type": "extends", "confidence": 0.9}
        ],
        "relations_in": [
            {"id": str(uuid4()), "source": SOURCE, "type": "updates", "confidence": None}
        ],
    }
    return GraphEvent(
        event_id=str(uuid4()),
        seq=seq,
        occurred_at="2026-10-29T00:00:00+00:00",
        workspace_id=workspace_id,
        type=type_,
        aggregate_id=MEM,
        payload={**base, **(payload or {})},
    )


class Recorder:
    def __init__(self, *, claim: bool = True) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._claim = claim

    async def __call__(self, query: str, params: dict):
        self.calls.append((query, params))
        if query is gm.CLAIM_MEMORY:
            return [{"key": params["mkey"]}] if self._claim else []
        return []

    def queries(self) -> list[str]:
        return [q for q, _ in self.calls]


@pytest.mark.unit
def test_every_statement_is_scoped_to_the_workspace() -> None:
    for statement in gm.ALL_STATEMENTS:
        assert "$workspace_id" in statement


@pytest.mark.unit
def test_cypher_is_constant_text_without_interpolation() -> None:
    for statement in gm.ALL_STATEMENTS:
        assert "{{" not in statement and "%s" not in statement
        assert "{key: $" in statement or "$rows" in statement or "$mkey" in statement


@pytest.mark.unit
async def test_full_event_applies_in_a_fixed_order_and_advances_the_watermark() -> None:
    run = Recorder()
    assert await gm.apply_event(run, _event()) is True
    assert run.queries() == [
        gm.CLAIM_MEMORY,
        gm.DELETE_PARENT,
        gm.MERGE_PARENT,
        gm.DELETE_CONTRIBUTIONS,
        gm.MERGE_CONTRIBUTIONS,
        gm.DELETE_RELATIONS_OUT,
        gm.MERGE_RELATIONS_OUT,
        gm.MERGE_RELATIONS_IN,
        gm.ADVANCE_WATERMARK,
    ]


@pytest.mark.unit
async def test_a_stale_or_duplicate_event_stops_after_the_claim() -> None:
    run = Recorder(claim=False)
    assert await gm.apply_event(run, _event()) is False
    assert run.queries() == [gm.CLAIM_MEMORY]  # nothing deleted, nothing merged, no watermark


@pytest.mark.unit
async def test_keys_embed_the_workspace_and_params_carry_it() -> None:
    run = Recorder()
    await gm.apply_event(run, _event())
    for _, params in run.calls:
        assert params["workspace_id"] == WS
    claim = run.calls[0][1]
    assert claim["mkey"] == f"{WS}:{MEM}"
    parent = next(p for q, p in run.calls if q is gm.MERGE_PARENT)
    assert parent["pkey"] == f"{WS}:{PARENT}"
    assert run.calls[4][1]["rows"][0]["ukey"] == f"{WS}:{USER}"
    assert run.calls[6][1]["rows"][0]["tkey"] == f"{WS}:{TARGET}"
    assert run.calls[7][1]["rows"][0]["skey"] == f"{WS}:{SOURCE}"


@pytest.mark.unit
async def test_the_same_ids_in_two_workspaces_never_share_a_node_key() -> None:
    a, b = Recorder(), Recorder()
    await gm.apply_event(a, _event(workspace_id=WS))
    await gm.apply_event(b, _event(workspace_id=OTHER_WS))
    assert a.calls[0][1]["mkey"] != b.calls[0][1]["mkey"]


@pytest.mark.unit
async def test_absent_parent_and_empty_lists_skip_their_merges_but_still_replace() -> None:
    event = _event(
        {
            "memory": {"id": MEM, "version": 1, "parent_id": None, "current": True},
            "contributors": [],
            "relations_out": [],
            "relations_in": [],
        }
    )
    run = Recorder()
    await gm.apply_event(run, event)
    assert gm.MERGE_PARENT not in run.queries()
    assert (
        gm.MERGE_CONTRIBUTIONS not in run.queries() and gm.MERGE_RELATIONS_OUT not in run.queries()
    )
    # Replacing wholesale means the deletes still run, so a removed edge disappears.
    assert gm.DELETE_PARENT in run.queries()
    assert gm.DELETE_CONTRIBUTIONS in run.queries() and gm.DELETE_RELATIONS_OUT in run.queries()


@pytest.mark.unit
async def test_hostile_values_travel_as_parameters_never_as_query_text() -> None:
    hostile = "x' }) DETACH DELETE m //"
    event = _event(
        {
            "contributors": [{"user_id": USER, "weight": 1, "action": hostile, "edit_id": hostile}],
            "relations_out": [
                {"id": str(uuid4()), "target": TARGET, "type": hostile, "confidence": 1}
            ],
        }
    )
    run = Recorder()
    await gm.apply_event(run, event)
    assert all(hostile not in query for query in run.queries())
    assert any(hostile in str(params) for _, params in run.calls)


@pytest.mark.unit
@pytest.mark.parametrize(
    "field", ["memory.id", "parent", "user", "target", "relation_id", "source"]
)
async def test_non_uuid_identifiers_are_rejected_before_any_write(field: str) -> None:
    payload = {}
    if field == "memory.id":
        payload["memory"] = {"id": "not-a-uuid", "version": 1}
    elif field == "parent":
        payload["memory"] = {"id": MEM, "version": 1, "parent_id": "nope"}
    elif field == "user":
        payload["contributors"] = [{"user_id": "nope", "weight": 1}]
    elif field == "target":
        payload["relations_out"] = [{"id": str(uuid4()), "target": "nope"}]
    elif field == "relation_id":
        payload["relations_out"] = [{"id": "nope", "target": TARGET}]
    else:
        payload["relations_in"] = [{"id": str(uuid4()), "source": "nope"}]
    with pytest.raises(gm.InvalidEventError):
        await gm.apply_event(Recorder(), _event(payload))


@pytest.mark.unit
async def test_an_unknown_event_type_is_ignored_not_applied() -> None:
    run = Recorder()
    assert await gm.apply_event(run, _event(type_="memory.something_new")) is False
    assert run.calls == []


@pytest.mark.unit
async def test_missing_memory_block_is_an_invalid_event() -> None:
    event = _event()
    bad = GraphEvent(**{**event.__dict__, "payload": {"contributors": []}})
    with pytest.raises(gm.InvalidEventError):
        await gm.apply_event(Recorder(), bad)


@pytest.mark.unit
async def test_a_deleted_memory_is_flagged_not_removed() -> None:
    event = _event(
        {
            "memory": {
                "id": MEM,
                "version": 1,
                "current": False,
                "deleted": True,
                "deleted_reason": "user_deleted",
            }
        }
    )
    run = Recorder()
    await gm.apply_event(run, event)
    claim = run.calls[0][1]
    assert claim["deleted"] is True and claim["deleted_reason"] == "user_deleted"
    assert not any("DETACH DELETE" in q or "DELETE m" in q for q in run.queries())


# ── dead-letter gaps and backfill markers (D-062) ───────────────────────────────


def _marker(type_, payload=None, *, seq=100) -> GraphEvent:
    return GraphEvent(
        event_id=str(uuid4()),
        seq=seq,
        occurred_at="2026-10-29T00:00:00+00:00",
        workspace_id=WS,
        type=type_,
        aggregate_id=WS,
        payload=payload or {},
    )


@pytest.mark.unit
async def test_marking_a_gap_keeps_the_highest_lost_seq_and_is_workspace_scoped() -> None:
    run = Recorder()
    await gm.mark_gap(run, WS, 50)
    (query, params), = run.calls
    assert query is gm.MARK_GAP and params == {"workspace_id": WS, "seq": 50}
    assert "$workspace_id" in gm.MARK_GAP and "gap_seq" in gm.MARK_GAP
    with pytest.raises(gm.InvalidEventError):
        await gm.mark_gap(run, "not-a-uuid", 1)


@pytest.mark.unit
async def test_the_started_marker_only_advances_the_watermark() -> None:
    run = Recorder()
    assert await gm.apply_event(run, _marker("workspace.backfill_started", seq=90)) is True
    assert [q for q, _ in run.calls] == [gm.ADVANCE_WATERMARK]
    assert run.calls[0][1]["seq"] == 90


@pytest.mark.unit
async def test_the_complete_marker_clears_only_older_gaps_then_advances_the_watermark() -> None:
    run = Recorder()
    event = _marker("workspace.backfill_complete", {"started_seq": 80, "emitted": 3}, seq=95)
    assert await gm.apply_event(run, event) is True
    assert [q for q, _ in run.calls] == [gm.CLEAR_GAP, gm.ADVANCE_WATERMARK]
    assert run.calls[0][1] == {"workspace_id": WS, "seq": 95, "started_seq": 80}
    # The clause that makes the repair provable: a newer gap (a backfill event that itself
    # dead-lettered) is left alone.
    assert "w.gap_seq < $started_seq" in gm.CLEAR_GAP


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload", [{}, {"started_seq": 0}, {"started_seq": "80"}, {"started_seq": True}]
)
async def test_a_complete_marker_without_a_usable_started_seq_is_invalid(payload) -> None:
    run = Recorder()
    with pytest.raises(gm.InvalidEventError):
        await gm.apply_event(run, _marker("workspace.backfill_complete", payload))
    assert run.calls == []


@pytest.mark.unit
def test_every_statement_including_the_gap_ones_is_scoped_to_a_workspace() -> None:
    assert gm.MARK_GAP in gm.ALL_STATEMENTS and gm.CLEAR_GAP in gm.ALL_STATEMENTS
    assert all("$workspace_id" in statement for statement in gm.ALL_STATEMENTS)
