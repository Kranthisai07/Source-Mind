"""Contribution history across memory versions, and non-content updates.

These use a small in-memory session double, so they prove what is *supplied to
the scorer* and which branch PATCH takes. They do not prove SQL semantics; the
real-PostgreSQL counterpart is tests/integration/test_attribution_history_real_db.py.

Scorer output is deterministic (equal split). No test asserts a particular
share for any editor: the scoring policy is out of scope for this change.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sourcemind.models.attribution import Attribution, AttributionEdit
from sourcemind.services.attribution import engine

ALICE = str(uuid.UUID(int=1))
BOB = str(uuid.UUID(int=2))
WS = uuid.UUID(int=99)


class _FakeScorer:
    """Records the exact events it receives; returns an equal split."""

    def __init__(self) -> None:
        self.received: list[list] = []

    def compute_scores(self, edits):
        self.received.append(list(edits))
        users = list(dict.fromkeys(e.user_id for e in edits))
        return [
            SimpleNamespace(
                user_id=u,
                contribution_weight=1 / len(users),
                char_diff_score=0.5,
                semantic_score=0.5,
                temporal_score=0.5,
                structural_score=0.5,
                approval_score=0.0,
            )
            for u in users
        ]


class _FakeSession:
    """Emulates the two history queries and records adds.

    ``structure`` is what the ancestry query returns, as
    (id, parent_memory_id, version, depth, cycle) tuples; ``edit_rows`` is what
    the edits query returns, as
    (edit_id, memory_id, editor, before, after, position, action, created_at).
    """

    def __init__(self, structure: list[tuple], edit_rows: list[tuple]) -> None:
        self.structure = structure
        self.edit_rows = edit_rows
        self.added: list = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        result = MagicMock()
        if "WITH RECURSIVE" in sql:
            result.fetchall.return_value = self.structure
        elif "FROM attribution_edits ae" in sql:
            result.fetchall.return_value = self.edit_rows
        else:  # pragma: no cover
            raise AssertionError(f"unexpected SQL: {sql}")
        return result

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()


_BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _chain(n: int) -> tuple[list[uuid.UUID], list[tuple]]:
    """A complete linear chain of ``n`` memories, current first, root last."""
    ids = [uuid.uuid4() for _ in range(n)]
    rows = [
        (ids[d], ids[d + 1] if d + 1 < n else None, n - d, d, False) for d in range(n)
    ]
    return ids, rows


def _edits_query_row(memory_id, user, before, after, pos, action="create", minute=0):
    return (uuid.uuid4(), memory_id, user, before, after, pos, action,
            _BASE.replace(minute=minute))


def _session_for(n_versions: int, edits: list[tuple]):
    """``edits`` are (version_index_from_root, user, before, after, pos, action)."""
    ids, rows = _chain(n_versions)
    by_version = {n_versions - d: ids[d] for d in range(n_versions)}  # version -> id
    edit_rows = [
        _edits_query_row(by_version[v], user, before, after, pos, action, minute=i)
        for i, (v, user, before, after, pos, action) in enumerate(edits)
    ]
    return ids[0], _FakeSession(rows, edit_rows)


@pytest.fixture
def scorer():
    s = _FakeScorer()
    with patch.object(engine, "get_scorer", return_value=s):
        yield s


@pytest.mark.unit
@pytest.mark.asyncio
async def test_alice_creates_bob_edits_scorer_sees_both_events_once(scorer):
    current, session = _session_for(2, [(1, ALICE, None, "v1", 1, "create")])

    await engine.recompute_attribution(
        session, current, uuid.UUID(BOB), "v1", "v2", "edit", str(uuid.uuid4())
    )

    events = scorer.received[0]
    assert [(e.user_id, e.content_before, e.content_after, e.edit_position) for e in events] == [
        (ALICE, None, "v1", 1),
        (BOB, "v1", "v2", 2),
    ]
    new_edits = [o for o in session.added if isinstance(o, AttributionEdit)]
    assert len(new_edits) == 1 and new_edits[0].edit_position == 2
    snapshot_users = {str(o.user_id) for o in session.added if isinstance(o, Attribution)}
    assert snapshot_users == {ALICE, BOB}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_alice_bob_alice_all_three_events_once_in_order(scorer):
    current, session = _session_for(
        3, [(1, ALICE, None, "v1", 1, "create"), (2, BOB, "v1", "v2", 2, "edit")]
    )

    await engine.recompute_attribution(
        session, current, uuid.UUID(ALICE), "v2", "v3", "edit", str(uuid.uuid4())
    )

    events = scorer.received[0]
    assert [(e.user_id, e.edit_position) for e in events] == [(ALICE, 1), (BOB, 2), (ALICE, 3)]
    assert [e.content_after for e in events] == ["v1", "v2", "v3"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_duplicate_rows_for_one_edit_are_collapsed(scorer):
    current, session = _session_for(2, [(1, ALICE, None, "v1", 1, "create")])
    session.edit_rows = session.edit_rows * 2  # the same edit returned twice

    await engine.recompute_attribution(session, current, uuid.UUID(BOB), "v1", "v2", "edit", None)

    assert len(scorer.received[0]) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_legacy_restarted_positions_reach_the_scorer_chronologically(scorer):
    """Pre-fix versions each restarted at position 1. The scorer's temporal signal
    reads the position itself, so it must be given consecutive chronological
    positions; the position persisted for the new edit keeps its audit rule."""
    current, session = _session_for(
        3, [(1, ALICE, None, "v1", 1, "create"), (2, BOB, "v1", "v2", 1, "edit")]
    )

    await engine.recompute_attribution(
        session, current, uuid.UUID(ALICE), "v2", "v3", "edit", None
    )

    events = scorer.received[0]
    assert [(e.user_id, e.edit_position) for e in events] == [(ALICE, 1), (BOB, 2), (ALICE, 3)]
    persisted = [o for o in session.added if isinstance(o, AttributionEdit)]
    assert [e.edit_position for e in persisted] == [3]  # max(2 events, highest recorded 1) + 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_later_editor_is_not_given_first_event_credit_by_restarted_positions():
    """Run the REAL scorer (SBERT mocked, as elsewhere). Bob is the second event, so
    his temporal primacy is 0.8; with the restarted recorded position it would be 1.0."""
    import numpy as np

    from sourcemind.services.attribution.scorer import AttributionScorer

    real = AttributionScorer()
    sbert = MagicMock()
    sbert.encode = MagicMock(return_value=np.array([[1.0, 0.0], [1.0, 0.0]]))
    real._sbert = sbert
    current, session = _session_for(
        3, [(1, ALICE, None, "Alice wrote the first draft.", 1, "create"),
            (2, BOB, "Alice wrote the first draft.", "Alice wrote the first draft. Bob added.",
             1, "edit")]
    )

    with patch.object(engine, "get_scorer", return_value=real):
        await engine.recompute_attribution(
            session, current, uuid.UUID(ALICE), "Alice wrote the first draft. Bob added.",
            "Alice wrote the first draft. Bob added. Alice again.", "edit", None,
        )

    temporal = {str(a.user_id): a.temporal_score
                for a in session.added if isinstance(a, Attribution)}
    assert temporal[BOB] == pytest.approx(0.8)  # second event, not first
    assert temporal[ALICE] == pytest.approx((1.0 + 0.8**2) / 2)  # events 1 and 3


# ── ancestry validation ──────────────────────────────────────────────────────


def _structure_session(rows):
    return _FakeSession(rows, [])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_complete_chain_returns_every_version():
    ids, rows = _chain(4)
    versions = await engine._load_ancestry(_structure_session(rows), ids[0])
    assert versions == {ids[0]: 4, ids[1]: 3, ids[2]: 2, ids[3]: 1}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chain_exactly_at_the_bound_is_accepted_and_one_more_is_refused(monkeypatch):
    monkeypatch.setattr(engine, "MAX_CHAIN_DEPTH", 3)
    ids, rows = _chain(4)  # depths 0..3: three ancestors
    assert len(await engine._load_ancestry(_structure_session(rows), ids[0])) == 4

    ids5, rows5 = _chain(5)  # the traversal may emit depth 4 (limit + 1) to prove overflow
    with pytest.raises(_conflict()) as exc:
        await engine._load_ancestry(_structure_session(rows5), ids5[0])
    assert "ancestry_overflow" in str(exc.value)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reserve_counts_the_version_about_to_be_created(monkeypatch):
    monkeypatch.setattr(engine, "MAX_CHAIN_DEPTH", 3)
    ids, rows = _chain(4)  # already three ancestors
    await engine._load_ancestry(_structure_session(rows), ids[0], reserve=0)
    with pytest.raises(_conflict()) as exc:
        await engine._load_ancestry(_structure_session(rows), ids[0], reserve=1)
    assert "ancestry_overflow" in str(exc.value)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cycle_in_parent_links_is_refused():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rows = [(a, b, 3, 0, False), (b, c, 2, 1, False), (c, a, 1, 2, False), (a, b, 3, 3, True)]
    with pytest.raises(_conflict()) as exc:
        await engine._load_ancestry(_structure_session(rows), a)
    assert "ancestry_cycle" in str(exc.value)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chain_whose_parent_cannot_be_reached_is_refused_not_truncated():
    ids, rows = _chain(3)
    rows[-1] = (rows[-1][0], uuid.uuid4(), rows[-1][2], rows[-1][3], False)  # root has a parent
    with pytest.raises(_conflict()) as exc:
        await engine._load_ancestry(_structure_session(rows), ids[0])
    assert "ancestry_incomplete" in str(exc.value)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_refused_ancestry_never_reaches_the_scorer_or_writes(scorer):
    a, b = uuid.uuid4(), uuid.uuid4()
    session = _FakeSession([(a, b, 2, 0, False), (b, a, 1, 1, False), (a, b, 2, 2, True)], [])
    with pytest.raises(_conflict()):
        await engine.recompute_attribution(
            session, a, uuid.UUID(BOB), "x", "y", "edit", None
        )
    assert scorer.received == [] and session.added == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_edit_guard_reserves_headroom_for_the_version_it_precedes():
    with (
        patch.object(engine, "plan_carry_forward", new=AsyncMock(return_value=[])),
        patch.object(engine, "_load_chain_history", new=AsyncMock(return_value=[])) as load,
    ):
        await engine.assert_edit_preserves_inherited_contributors(MagicMock(), uuid.uuid4())
    assert load.await_args.kwargs["reserve"] == 1


# ── snapshot selection ───────────────────────────────────────────────────────

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 2, tzinfo=UTC)
EDIT_MEMORY = uuid.UUID(int=500)


def _snap(user, weight, created, *, action="create", edit_id=None, pos=None,
          edit_memory=None, **scores):
    values = {"char_diff_score": 0.1, "semantic_score": 0.2, "temporal_score": 0.3,
              "structural_score": 0.4, "approval_score": 0.0}
    values.update(scores)
    return SimpleNamespace(
        user_id=uuid.UUID(user),
        contribution_weight=weight,
        trigger_action=action,
        edit_id=edit_id,
        created_at=created,
        edit_position=pos,
        edit_memory_id=edit_memory if edit_id else None,
        **values,
    )


def _edit_row(user, weight, created, pos, action="edit", **kw):
    return _snap(user, weight, created, action=action, edit_id=uuid.uuid4(), pos=pos,
                 edit_memory=EDIT_MEMORY, **kw)


class _SnapSession:
    def __init__(self, rows):
        self.rows = rows
        self.added: list = []

    async def execute(self, statement, params=None):
        result = MagicMock()
        result.fetchall.return_value = self.rows
        return result

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


def _conflict():
    from sourcemind.core.exceptions import AttributionStateConflictError

    return AttributionStateConflictError


@pytest.mark.unit
@pytest.mark.asyncio
async def test_carry_forward_copies_newest_row_per_user_exactly_and_adds_no_event():
    older = _snap(ALICE, 1.0, T0)
    newest_a = _edit_row(ALICE, 0.6, T1, 2)
    newest_b = _edit_row(BOB, 0.4, T1, 2)
    session = _SnapSession([older, newest_a, newest_b])
    new_id = uuid.uuid4()

    carried = await engine.carry_forward_attribution(session, uuid.uuid4(), new_id)

    by_user = {str(a.user_id): a for a in carried}
    assert set(by_user) == {ALICE, BOB}
    assert by_user[ALICE].contribution_weight == 0.6
    assert by_user[ALICE].trigger_action == "edit"  # original action preserved
    assert by_user[ALICE].edit_id == newest_a.edit_id
    assert all(a.memory_id == new_id for a in carried)
    assert not [o for o in session.added if isinstance(o, AttributionEdit)]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_identical_duplicates_differing_only_by_row_identity_collapse():
    eid = uuid.uuid4()
    a = _snap(ALICE, 0.5, T1, action="edit", edit_id=eid, pos=2, edit_memory=EDIT_MEMORY)
    b = _snap(ALICE, 0.5, T1, action="edit", edit_id=eid, pos=2, edit_memory=EDIT_MEMORY)
    carried = await engine.carry_forward_attribution(
        _SnapSession([a, b]), uuid.uuid4(), uuid.uuid4()
    )
    assert len(carried) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handoff_transfer_tied_with_edit_row_and_differing_shares_conflicts():
    """Edit-linked row (non-null position) vs transfer (no edit_id), same created_at."""
    edit_row = _edit_row(ALICE, 0.6, T1, 3)
    transfer = _snap(ALICE, 0.24, T1, action="transfer")
    session = _SnapSession([edit_row, transfer])

    with pytest.raises(_conflict()):
        await engine.carry_forward_attribution(session, uuid.uuid4(), uuid.uuid4())
    assert session.added == []
    with pytest.raises(_conflict()):
        await engine.plan_carry_forward(_SnapSession([transfer, edit_row]), uuid.uuid4())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_same_values_but_different_provenance_is_not_equivalent():
    merged = _snap(ALICE, 0.5, T1, action="merged")
    transfer = _snap(ALICE, 0.5, T1, action="transfer")
    with pytest.raises(_conflict()):
        await engine.plan_carry_forward(_SnapSession([merged, transfer]), uuid.uuid4())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_distinct_stored_floats_are_not_collapsed_by_a_tolerance():
    a = _snap(ALICE, 0.5, T1, action="merged")
    b = _snap(ALICE, 0.5 + 1e-12, T1, action="merged")
    with pytest.raises(_conflict()):
        await engine.plan_carry_forward(_SnapSession([a, b]), uuid.uuid4())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_two_edit_rows_of_one_memory_are_ordered_by_recorded_position():
    low = _edit_row(ALICE, 0.9, T1, 1)
    high = _edit_row(ALICE, 0.4, T1, 2)
    for order in ([low, high], [high, low]):  # selection must not depend on input order
        chosen = await engine.plan_carry_forward(_SnapSession(order), uuid.uuid4())
        assert [r.contribution_weight for r in chosen] == [0.4]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_edit_rows_with_equal_positions_or_foreign_memories_do_not_order():
    with pytest.raises(_conflict()):
        await engine.plan_carry_forward(
            _SnapSession([_edit_row(ALICE, 0.9, T1, 2), _edit_row(ALICE, 0.4, T1, 2)]),
            uuid.uuid4(),
        )
    foreign = _snap(ALICE, 0.4, T1, action="edit", edit_id=uuid.uuid4(), pos=3,
                    edit_memory=uuid.uuid4())
    with pytest.raises(_conflict()):
        await engine.plan_carry_forward(
            _SnapSession([_edit_row(ALICE, 0.9, T1, 2), foreign]), uuid.uuid4()
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_older_rows_never_make_the_newest_ambiguous():
    rows = [_snap(ALICE, 0.1, T0, action="merged"), _snap(ALICE, 0.2, T0, action="transfer"),
            _edit_row(ALICE, 0.7, T1, 1)]
    chosen = await engine.plan_carry_forward(_SnapSession(rows), uuid.uuid4())
    assert [r.contribution_weight for r in chosen] == [0.7]


# ── inherited-contributor guard (provenance, per selected row) ───────────────


def _guard_fixture(rows, history):
    """``rows`` are the SELECTED snapshot rows; ``history`` the loaded ancestry."""
    return (
        patch.object(engine, "plan_carry_forward", new=AsyncMock(return_value=rows)),
        patch.object(engine, "_load_chain_history", new=AsyncMock(return_value=history)),
    )


def _hist(editor, pos, action="edit"):
    return engine._HistoryRow(uuid.uuid4(), editor, None, "x", pos, action)


def _batch_row(user, hist_row, weight=0.5, action=None):
    """A row the engine would have written for the edit ``hist_row``."""
    return SimpleNamespace(
        user_id=uuid.UUID(user), contribution_weight=weight,
        trigger_action=action or hist_row.action_type,
        edit_id=hist_row.edit_id, edit_action_type=hist_row.action_type,
        edit_memory_id=EDIT_MEMORY, edit_position=hist_row.edit_position,
        created_at=T1,
    )


def _snapshot_only_row(user, action, weight=0.4, edit_id=None, edit_action_type=None):
    return SimpleNamespace(
        user_id=uuid.UUID(user), contribution_weight=weight, trigger_action=action,
        edit_id=edit_id, edit_action_type=edit_action_type,
        edit_memory_id=EDIT_MEMORY if edit_id else None,
        edit_position=None, created_at=T1,
    )


async def _run_guard(rows, history):
    p1, p2 = _guard_fixture(rows, history)
    with p1, p2:
        await engine.assert_edit_preserves_inherited_contributors(MagicMock(), uuid.uuid4())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_guard_allows_rows_the_recorded_history_reproduces():
    create = _hist(ALICE, 1, "create")
    edit = _hist(BOB, 2, "edit")
    # Engine-written batch rows for the second edit cover both contributors.
    await _run_guard([_batch_row(ALICE, edit), _batch_row(BOB, edit)], [create, edit])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_guard_allows_unresolved_external_origin_with_no_snapshot_rows():
    await _run_guard([], [])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_guard_refuses_a_transfer_to_a_user_who_also_genuinely_edited():
    """Identity overlap must not make a transferred share representable."""
    create = _hist(ALICE, 1, "create")
    bob_edit = _hist(BOB, 2, "edit")
    transfer = _snapshot_only_row(BOB, "transfer")  # Bob IS an editor in the history
    assert BOB in {h.editor_id for h in (create, bob_edit)}
    with pytest.raises(_conflict()):
        await _run_guard([_batch_row(ALICE, bob_edit), transfer], [create, bob_edit])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_guard_refuses_a_transfer_even_when_stamped_with_a_valid_edit_id():
    create = _hist(ALICE, 1, "create")
    bob_edit = _hist(BOB, 2, "edit")
    stamped = _snapshot_only_row(BOB, "transfer", edit_id=bob_edit.edit_id,
                                 edit_action_type="edit")
    with pytest.raises(_conflict()):
        await _run_guard([stamped], [create, bob_edit])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_guard_refuses_merged_rows_and_foreign_edit_links():
    create = _hist(ALICE, 1, "create")
    with pytest.raises(_conflict()):
        await _run_guard([_snapshot_only_row(ALICE, "merged")], [create])
    foreign = _snapshot_only_row(ALICE, "create", edit_id=uuid.uuid4(),
                                 edit_action_type="create")  # not in this ancestry
    with pytest.raises(_conflict()):
        await _run_guard([foreign], [create])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_guard_refuses_a_user_who_never_edited_even_with_a_consistent_row():
    create = _hist(ALICE, 1, "create")
    with pytest.raises(_conflict()):
        await _run_guard([_batch_row(BOB, create)], [create])


# ── PATCH branching ──────────────────────────────────────────────────────────


def _current(content="same text", tags=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        workspace_id=WS,
        document_id=None,
        content=content,
        version=3,
        tags=tags if tags is not None else ["a"],
        category=None,
        confidence_score=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=None,
    )


async def _patch(content, tags, current, *, guard_error=None, plan_error=None):
    """Run update_memory with collaborators mocked; returns (response|error, mocks)."""
    from sourcemind.api.v1 import memories
    from sourcemind.schemas.memory import MemoryUpdate

    db = AsyncMock()
    found = MagicMock()
    found.scalar_one_or_none.return_value = current
    db.execute = AsyncMock(return_value=found)
    new_mem = _current(content, tags)
    new_mem.version = 4
    vr = SimpleNamespace(new_memory=new_mem, previous_version_id=current.id)

    manager = MagicMock()
    mocks = {
        "guard": AsyncMock(side_effect=guard_error),
        "plan": AsyncMock(side_effect=plan_error),
        "cnv": AsyncMock(return_value=vr),
        "recompute": AsyncMock(),
        "carry": AsyncMock(),
        "auth": AsyncMock(),
        "manager": manager,
    }
    for name in ("guard", "plan", "cnv"):
        manager.attach_mock(mocks[name], name)
    outcome = None
    with (
        patch.object(memories, "require_memory_access", new=mocks["auth"]),
        patch("sourcemind.services.attribution.versioning.create_new_version", new=mocks["cnv"]),
        patch("sourcemind.services.attribution.engine.recompute_attribution",
              new=mocks["recompute"]),
        patch("sourcemind.services.attribution.engine.carry_forward_attribution",
              new=mocks["carry"]),
        patch("sourcemind.services.attribution.engine.plan_carry_forward", new=mocks["plan"]),
        patch("sourcemind.services.attribution.engine."
              "assert_edit_preserves_inherited_contributors", new=mocks["guard"]),
        patch("sourcemind.services.memory.importance.recompute_importance", new=AsyncMock()),
        patch("sourcemind.services.conflict.severity.recompute_severity_for_memory",
              new=AsyncMock()),
    ):
        try:
            outcome = await memories.update_memory(
                memory_id=current.id,
                body=MemoryUpdate(content=content, tags=tags),
                db=db,
                current_user=SimpleNamespace(user_id=uuid.UUID(BOB)),
                request_id="r",
                idempotency_key=str(uuid.uuid4()),
                openai_client=None,
            )
        except _conflict() as exc:
            outcome = exc
    mocks["db"] = db
    return outcome, mocks


@pytest.mark.unit
@pytest.mark.asyncio
async def test_exact_unchanged_save_creates_no_version_and_no_event():
    cur = _current("same text", ["a"])
    resp, m = await _patch("same text", None, cur)
    m["auth"].assert_awaited_once()  # authorization precedes the no-op return
    for name in ("cnv", "recompute", "carry", "guard", "plan"):
        m[name].assert_not_awaited()
    assert resp.data.id == cur.id and resp.data.version == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tags_only_update_is_planned_before_the_version_and_carries_without_event():
    cur = _current("same text", ["a"])
    resp, m = await _patch("same text", ["a", "b"], cur)
    assert [c[0] for c in m["manager"].mock_calls] == ["plan", "cnv"]
    m["recompute"].assert_not_awaited()
    m["guard"].assert_not_awaited()
    m["carry"].assert_awaited_once()
    assert m["carry"].await_args.kwargs["from_memory_id"] == cur.id
    assert resp.data.version == 4


@pytest.mark.unit
@pytest.mark.asyncio
async def test_equal_length_replacement_is_a_real_edit_guarded_before_the_version():
    cur = _current("abcd efgh", ["a"])
    _, m = await _patch("wxyz ijkl", None, cur)
    assert len("abcd efgh") == len("wxyz ijkl")
    assert [c[0] for c in m["manager"].mock_calls] == ["guard", "cnv"]
    m["recompute"].assert_awaited_once()
    m["carry"].assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["content", "tags"])
async def test_refusal_happens_before_any_version_or_attribution_write(kind):
    cur = _current("same text", ["a"])
    err = _conflict()("refused")
    if kind == "content":
        outcome, m = await _patch("different text", None, cur, guard_error=err)
    else:
        outcome, m = await _patch("same text", ["b"], cur, plan_error=err)
    assert isinstance(outcome, _conflict())
    for name in ("cnv", "recompute", "carry"):
        m[name].assert_not_awaited()
    m["db"].commit.assert_not_awaited()
