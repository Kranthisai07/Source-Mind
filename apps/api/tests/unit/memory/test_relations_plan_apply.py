"""RelationDetector plan/apply split (D-021 round 2).

The ingestion worker takes a FOR SHARE lock on the GitHub author link and
must hold it only across database statements. Relation detection is
therefore split into:

  plan()  -- candidate vector query + LLM classification; writes NOTHING
  apply() -- conflict creation with the precomputed verdict, relation
             inserts, and importance/severity recompute;
             NEVER calls the model provider

detect() remains plan-and-apply interleaved exactly as before for its
existing callers (tests/unit/memory/test_relations.py is unchanged).
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from sourcemind.models.memory_conflict import MemoryConflict
from sourcemind.models.memory_relation import MemoryRelation
from sourcemind.services.memory.relations import (
    _CONFLICT_RADIUS,
    _LLM_RADIUS,
    _SCAN_RADIUS,
    RelationDetector,
)


def _memory(content: str = "a fact"):
    m = MagicMock()
    m.id = uuid.uuid4()
    m.content = content
    m.embedding = [0.1] * 8
    m.workspace_id = uuid.uuid4()
    return m


def _client(relation: str, confidence: float, is_conflict: bool = False):
    payload = json.dumps(
        {
            "relation": relation,
            "confidence": confidence,
            "is_conflict": is_conflict,
            "conflict_summary": "A and B disagree",
        }
    )
    client = MagicMock()
    client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text=payload)])
    )
    return client


class _ForbiddenProvider:
    """A provider client that fails the test if anything touches it."""

    @property
    def messages(self):
        raise AssertionError("apply() must never call the model provider")


def _session(candidates, *, contributors: dict[str, str] | None = None):
    """Fake session: candidate query, per-memory contributor, no existing edge."""
    contributors = contributors or {}
    statements: list[tuple[str, dict]] = []
    added: list = []

    async def execute(stmt, params=None, **_k):
        sql = " ".join(str(stmt).split())
        statements.append((sql, dict(params or {})))
        r = MagicMock()
        if "embedding <=>" in sql:
            excluded = set((params or {}).get("excluded") or [])
            r.fetchall.return_value = [c for c in candidates if c[0] not in excluded]
        elif "FROM attributions" in sql:
            user = contributors.get((params or {}).get("id"))
            r.fetchone.return_value = (user,) if user else None
        else:
            r.fetchone.return_value = None
            r.fetchall.return_value = []
        return r

    nested = AsyncMock()
    nested.__aenter__ = AsyncMock(return_value=nested)
    nested.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.execute = AsyncMock(side_effect=execute)
    session.add = MagicMock(side_effect=added.append)
    session.flush = AsyncMock()
    session.begin_nested = MagicMock(return_value=nested)
    session.statements = statements
    session.added = added
    return session


@pytest.fixture(autouse=True)
def _no_rescoring(monkeypatch):
    from sourcemind.services.conflict import severity
    from sourcemind.services.memory import importance

    monkeypatch.setattr(severity, "recompute_severity_for_memory", AsyncMock())
    monkeypatch.setattr(importance, "recompute_importance", AsyncMock())


@pytest.mark.unit
async def test_plan_classifies_but_writes_nothing() -> None:
    near, mid, far = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    session = _session(
        [
            (near, "near", _LLM_RADIUS - 0.05),
            (mid, "scan only", (_LLM_RADIUS + _SCAN_RADIUS) / 2),
            (far, "far", _SCAN_RADIUS + 0.05),
        ]
    )
    client = _client("extends", 0.95)
    memory = _memory()

    planned = await RelationDetector(client).plan(session, [memory], uuid.uuid4())

    assert [(p.cand_id, p.verdict.relation) for p in planned] == [
        (uuid.UUID(near), "extends")
    ]
    assert planned[0].memory is memory
    client.messages.create.assert_awaited_once()
    assert session.added == []
    session.begin_nested.assert_not_called()
    session.flush.assert_not_awaited()
    assert all(sql.startswith("SELECT") for sql, _p in session.statements)


@pytest.mark.unit
async def test_apply_writes_relation_and_conflict_without_the_provider() -> None:
    cand = str(uuid.uuid4())
    memory = _memory()
    session = _session(
        [(cand, "rival claim", _CONFLICT_RADIUS - 0.05)],
        contributors={str(memory.id): "user-a", cand: "user-b"},
    )
    detector = RelationDetector(_client("extends", 0.95, is_conflict=True))
    planned = await detector.plan(session, [memory], uuid.uuid4())

    detector._client = _ForbiddenProvider()
    await detector.apply(session, [memory], planned)

    kinds = [type(obj) for obj in session.added]
    assert kinds == [MemoryConflict, MemoryRelation]


@pytest.mark.unit
async def test_apply_without_contributors_creates_no_conflict() -> None:
    cand = str(uuid.uuid4())
    memory = _memory()
    session = _session(
        [(cand, "rival claim", _CONFLICT_RADIUS - 0.05)],
        contributors={cand: "user-b"},  # the new memory has no attribution row
    )
    detector = RelationDetector(_client("unrelated", 0.95, is_conflict=True))
    planned = await detector.plan(session, [memory], uuid.uuid4())

    detector._client = _ForbiddenProvider()
    await detector.apply(session, [memory], planned)

    assert session.added == []


@pytest.mark.unit
async def test_plan_keeps_update_targets_visible_to_later_memories() -> None:
    target = str(uuid.uuid4())
    first, second = _memory("first"), _memory("second")
    session = _session([(target, "old value", _LLM_RADIUS - 0.05)])
    client = _client("updates", 0.95)

    planned = await RelationDetector(client).plan(session, [first, second], uuid.uuid4())

    assert [(p.memory, p.cand_id) for p in planned] == [
        (first, uuid.UUID(target)),
        (second, uuid.UUID(target)),
    ]
    candidate_queries = [p for sql, p in session.statements if "embedding <=>" in sql]
    assert "excluded" not in candidate_queries[0]
    assert "excluded" not in candidate_queries[1]
    assert client.messages.create.await_count == 2


@pytest.mark.unit
async def test_cross_user_update_keeps_relation_without_retiring_target() -> None:
    target = str(uuid.uuid4())
    memory = _memory("replacement wording")
    session = _session(
        [(target, "original wording", _CONFLICT_RADIUS - 0.01)],
        contributors={str(memory.id): "user-b", target: "user-a"},
    )
    detector = RelationDetector(_client("updates", 0.95))

    planned = await detector.plan(session, [memory], uuid.uuid4())
    await detector.apply(session, [memory], planned)

    relations = [item for item in session.added if isinstance(item, MemoryRelation)]
    assert len(relations) == 1
    assert relations[0].relation_type == "updates"
    assert not any(sql.startswith("UPDATE memories") for sql, _ in session.statements)


@pytest.mark.unit
async def test_detect_is_plan_then_apply_for_a_single_memory() -> None:
    cand = str(uuid.uuid4())
    via_detect, via_split = _memory(), _memory()
    contributors = {str(via_detect.id): "a", str(via_split.id): "a", cand: "b"}
    rows = [(cand, "rival", _CONFLICT_RADIUS - 0.05)]
    ws = uuid.uuid4()

    s1 = _session(rows, contributors=contributors)
    await RelationDetector(_client("extends", 0.95, True)).detect(s1, [via_detect], ws)
    s2 = _session(rows, contributors=contributors)
    detector = RelationDetector(_client("extends", 0.95, True))
    await detector.apply(s2, [via_split], await detector.plan(s2, [via_split], ws))

    def shape(session, memory):
        return [
            (sql, {k: v for k, v in p.items() if v != str(memory.id) and k != "emb"})
            for sql, p in session.statements
        ]

    assert shape(s1, via_detect) == shape(s2, via_split)
    assert [type(o) for o in s1.added] == [type(o) for o in s2.added]


@pytest.mark.unit
async def test_empty_plan_still_runs_post_processing(monkeypatch) -> None:
    from sourcemind.services.memory import importance

    rescore = AsyncMock()
    monkeypatch.setattr(importance, "recompute_importance", rescore)
    memory = _memory()
    detector = RelationDetector(_ForbiddenProvider())

    await detector.apply(_session([]), [memory], [])

    rescore.assert_awaited_once()
    assert rescore.await_args.args[1] == memory.id


@pytest.mark.unit
def test_planned_pair_carries_a_verdict_for_every_conflict_candidate() -> None:
    # apply() can only stay provider-free because every conflict candidate
    # is inside the LLM radius and therefore classified during plan().
    assert _CONFLICT_RADIUS <= _LLM_RADIUS
