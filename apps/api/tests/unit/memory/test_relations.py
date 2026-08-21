"""Tests for services/memory/relations.py — relation and conflict detection.

This module was at 0% coverage despite deciding what gets written to the
knowledge graph. Its three distance thresholds are the whole behaviour:

    _SCAN_RADIUS    0.25  beyond this, a candidate is ignored entirely
    _LLM_RADIUS     0.20  within this, Claude is asked to classify
    _CONFLICT_RADIUS 0.15 within this, a contradiction may become a conflict

and a fourth gate, _MIN_CONFIDENCE (0.85), decides whether the LLM's answer
is trusted at all. Getting any of these wrong writes wrong edges, and every
downstream traversal inherits the error.

Claude is mocked — the point is the branching logic around it, not the model.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from sourcemind.services.memory.relations import (
    _CONFLICT_RADIUS,
    _LLM_RADIUS,
    _MIN_CONFIDENCE,
    _SCAN_RADIUS,
    RelationDetector,
    _classify_relation,
)


def _memory(content: str = "a fact", embedding: list[float] | None = None):
    """A Memory-like stub. The real model needs a DB session to be useful."""
    m = MagicMock()
    m.id = uuid.uuid4()
    m.content = content
    m.embedding = embedding if embedding is not None else [0.1] * 3072
    m.workspace_id = uuid.uuid4()
    return m


def _claude_returning(relation: str, confidence: float, is_conflict: bool = False):
    """Anthropic client stub whose response parses to the given classification.

    is_conflict is separate from relation on purpose: a conflict is raised
    on mutual exclusivity, not on a supersession verdict.
    """
    client = MagicMock()
    payload = json.dumps(
        {
            "relation": relation,
            "confidence": confidence,
            "is_conflict": is_conflict,
            "conflict_summary": "A and B state different values",
        }
    )
    client.messages = MagicMock()
    client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text=payload)])
    )
    return client


def _session_with_candidates(rows: list[tuple[str, str, float]]):
    """Session whose neighbour query returns the given (id, content, distance)."""
    session = AsyncMock()
    added: list = []

    async def execute(stmt, params=None, **kwargs):
        r = MagicMock()
        r.fetchall = MagicMock(
            return_value=rows if "embedding <=>" in str(stmt) else []
        )
        r.fetchone = MagicMock(return_value=None)
        r.first = MagicMock(return_value=None)
        return r

    session.execute = AsyncMock(side_effect=execute)
    session.add = MagicMock(side_effect=added.append)
    session.flush = AsyncMock()

    # begin_nested() is used as an async context manager for the savepoint.
    nested = AsyncMock()
    nested.__aenter__ = AsyncMock(return_value=nested)
    nested.__aexit__ = AsyncMock(return_value=False)
    session.begin_nested = MagicMock(return_value=nested)

    session._added = added  # type: ignore[attr-defined]
    return session


# ─── _classify_relation ──────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_classify_parses_a_well_formed_response():
    verdict = await _classify_relation(
        _claude_returning("extends", 0.91), "existing", "new"
    )
    assert verdict.relation == "extends"
    assert verdict.confidence == 0.91
    assert verdict.is_conflict is False, (
        "a payload without is_conflict must not be read as a conflict"
    )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text_payload",
    ["not json at all", "", "{broken", "<html>rate limited</html>"],
    ids=["prose", "empty", "truncated", "html"],
)
async def test_classify_degrades_to_unrelated_on_unparseable_output(text_payload):
    """A model returning non-JSON must not raise into the pipeline.

    Note the consequence, which is why this is worth pinning: an outage and a
    genuine "these are unrelated" verdict are indistinguishable downstream.
    Both simply produce no edge.
    """
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text=text_payload)])
    )
    verdict = await _classify_relation(client, "a", "b")
    assert (verdict.relation, verdict.confidence) == ("unrelated", 0.0)
    assert verdict.is_conflict is False, (
        "an unparseable response must never raise a conflict"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_classify_degrades_when_the_api_call_itself_raises():
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(side_effect=RuntimeError("503 overloaded"))
    verdict = await _classify_relation(client, "a", "b")
    assert (verdict.relation, verdict.confidence) == ("unrelated", 0.0)
    assert verdict.is_conflict is False


# ─── distance gating ─────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_candidates_beyond_the_scan_radius_are_never_classified():
    """Past _SCAN_RADIUS the LLM must not be called at all — that is the cost
    control. Calling it for every neighbour would be the expensive bug."""
    far = _SCAN_RADIUS + 0.05
    client = _claude_returning("extends", 0.99)
    session = _session_with_candidates([(str(uuid.uuid4()), "far away", far)])

    await RelationDetector(client).detect(session, [_memory()], uuid.uuid4())

    client.messages.create.assert_not_called()
    assert session._added == [], "no relation should be written"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_candidates_between_scan_and_llm_radius_are_not_classified():
    """The band between _LLM_RADIUS and _SCAN_RADIUS is scanned but not sent
    to the model."""
    between = (_LLM_RADIUS + _SCAN_RADIUS) / 2
    client = _claude_returning("extends", 0.99)
    session = _session_with_candidates([(str(uuid.uuid4()), "middling", between)])

    await RelationDetector(client).detect(session, [_memory()], uuid.uuid4())

    client.messages.create.assert_not_called()
    assert session._added == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_close_candidate_produces_a_relation():
    close = _LLM_RADIUS - 0.05
    cand_id = str(uuid.uuid4())
    client = _claude_returning("extends", 0.95)
    session = _session_with_candidates([(cand_id, "very similar", close)])

    await RelationDetector(client).detect(session, [_memory()], uuid.uuid4())

    client.messages.create.assert_called_once()
    assert len(session._added) == 1, "expected one relation row"
    relation = session._added[0]
    assert relation.relation_type == "extends"
    assert relation.confidence == 0.95
    # similarity_score is the complement of cosine distance
    assert relation.similarity_score == pytest.approx(1.0 - close)


# ─── confidence gating ───────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_low_confidence_classification_is_discarded():
    """Below _MIN_CONFIDENCE the model's answer must not become an edge."""
    client = _claude_returning("extends", _MIN_CONFIDENCE - 0.10)
    session = _session_with_candidates(
        [(str(uuid.uuid4()), "similar", _LLM_RADIUS - 0.05)]
    )

    await RelationDetector(client).detect(session, [_memory()], uuid.uuid4())

    assert session._added == [], "a low-confidence guess was written to the graph"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unrelated_verdict_writes_nothing_even_at_high_confidence():
    client = _claude_returning("unrelated", 0.99)
    session = _session_with_candidates(
        [(str(uuid.uuid4()), "similar text", _LLM_RADIUS - 0.05)]
    )

    await RelationDetector(client).detect(session, [_memory()], uuid.uuid4())

    assert session._added == []


# ─── batch and shape handling ────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_memories_from_the_same_batch_are_not_linked_to_each_other():
    """Two memories extracted from one document must not be cross-linked.

    They come from the same source text, so they are trivially similar and
    would generate a burst of meaningless edges.
    """
    mem_a, mem_b = _memory("fact one"), _memory("fact two")
    client = _claude_returning("extends", 0.99)
    # The neighbour query returns the sibling from this same batch.
    session = _session_with_candidates([(str(mem_b.id), "fact two", 0.01)])

    await RelationDetector(client).detect(session, [mem_a, mem_b], uuid.uuid4())

    client.messages.create.assert_not_called()
    assert session._added == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_without_an_embedding_is_skipped():
    """A NULL embedding cannot be compared, and must not raise."""
    client = _claude_returning("extends", 0.99)
    session = _session_with_candidates([])
    no_embedding = _memory()
    no_embedding.embedding = None

    await RelationDetector(client).detect(session, [no_embedding], uuid.uuid4())

    client.messages.create.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_batch_is_a_no_op():
    client = _claude_returning("extends", 0.99)
    session = _session_with_candidates([])
    await RelationDetector(client).detect(session, [], uuid.uuid4())
    session.execute.assert_not_called()


# ─── threshold coherence ─────────────────────────────────────────────────────

@pytest.mark.unit
def test_thresholds_are_ordered_consistently():
    """The three radii must nest, or the gating logic contradicts itself.

    conflict ⊂ llm ⊂ scan. If _CONFLICT_RADIUS ever exceeded _LLM_RADIUS, a
    conflict could be considered for a pair the model was never asked about.
    """
    assert _CONFLICT_RADIUS < _LLM_RADIUS < _SCAN_RADIUS
    assert 0 < _MIN_CONFIDENCE <= 1.0
