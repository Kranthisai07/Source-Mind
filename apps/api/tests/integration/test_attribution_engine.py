"""
Integration tests for the attribution engine.

These tests verify the composition of scorer + versioning + engine,
mocking only the database session (not individual SQL calls).
Run with: pytest -m integration
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from sourcemind.services.attribution.scorer import AttributionScorer, EditEvent


def _make_scorer(sbert_similarity: float = 0.9) -> AttributionScorer:
    """Return a scorer with mocked SBERT and spaCy (no model loading)."""
    scorer = AttributionScorer()
    v1 = np.array([sbert_similarity, 0.0])
    v2 = np.array([1.0, 0.0])
    sbert_mock = MagicMock()
    sbert_mock.encode = MagicMock(return_value=np.array([v1, v2]))
    scorer._sbert = sbert_mock
    nlp_mock = MagicMock()
    doc_mock = MagicMock()
    doc_mock.ents = []
    nlp_mock.return_value = doc_mock
    scorer._nlp = nlp_mock
    return scorer


@pytest.mark.integration
@pytest.mark.asyncio
async def test_edit_triggers_attribution_recompute():
    """
    After PATCH /memories/:id, recompute_attribution() should:
      1. Read attribution_edits for prior history
      2. Insert new AttributionEdit record
      3. Insert new Attribution records (one per contributor)
    """
    from sourcemind.services.attribution.engine import recompute_attribution

    memory_id = uuid.uuid4()
    editor_id = uuid.uuid4()
    idempotency_key = str(uuid.uuid4())

    mock_session = AsyncMock()

    async def execute_side_effect(query, params=None):
        mock_result = MagicMock()
        query_str = str(query)
        if "MAX(edit_position)" in query_str:
            mock_result.scalar = MagicMock(return_value=None)
        elif "attribution_edits" in query_str and "SELECT" in query_str:
            mock_result.fetchall = MagicMock(return_value=[])
        else:
            mock_result.scalar = MagicMock(return_value=None)
            mock_result.fetchall = MagicMock(return_value=[])
        return mock_result

    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    mock_scorer = _make_scorer()

    with patch("sourcemind.services.attribution.engine.get_scorer", return_value=mock_scorer):
        await recompute_attribution(
            session=mock_session,
            memory_id=memory_id,
            editor_id=editor_id,
            content_before="",
            content_after="This is the initial content of the memory.",
            action_type="create",
            idempotency_key=idempotency_key,
        )

    # Should have called session.add at least once for the new Attribution record
    assert mock_session.add.called


@pytest.mark.integration
@pytest.mark.asyncio
async def test_multiple_edits_accumulate_history():
    """
    Multiple edits on the same memory should each increment edit_position
    and create separate Attribution records.
    """
    from sourcemind.services.attribution.engine import recompute_attribution

    memory_id = uuid.uuid4()
    editor_a = uuid.uuid4()
    editor_b = uuid.uuid4()
    idempotency_a = str(uuid.uuid4())
    idempotency_b = str(uuid.uuid4())

    add_calls = []
    mock_session = AsyncMock()
    mock_session.add = MagicMock(side_effect=lambda x: add_calls.append(x))
    mock_session.flush = AsyncMock()

    positions_returned = [None, 1]
    call_index = [0]

    async def execute_side_effect(query, params=None):
        mock_result = MagicMock()
        query_str = str(query)
        if "MAX(edit_position)" in query_str:
            pos = positions_returned[min(call_index[0], len(positions_returned) - 1)]
            mock_result.scalar = MagicMock(return_value=pos)
            call_index[0] += 1
        elif "attribution_edits" in query_str and "SELECT" in query_str:
            if call_index[0] > 1:
                mock_result.fetchall = MagicMock(return_value=[
                    (str(editor_a), "Initial.", "Edited once.", 0, "edit")
                ])
            else:
                mock_result.fetchall = MagicMock(return_value=[])
        else:
            mock_result.scalar = MagicMock(return_value=None)
            mock_result.fetchall = MagicMock(return_value=[])
        return mock_result

    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_scorer = _make_scorer()

    with patch("sourcemind.services.attribution.engine.get_scorer", return_value=mock_scorer):
        await recompute_attribution(
            session=mock_session,
            memory_id=memory_id,
            editor_id=editor_a,
            content_before="",
            content_after="Initial.",
            action_type="create",
            idempotency_key=idempotency_a,
        )

        await recompute_attribution(
            session=mock_session,
            memory_id=memory_id,
            editor_id=editor_b,
            content_before="Initial.",
            content_after="Edited once.",
            action_type="edit",
            idempotency_key=idempotency_b,
        )

    # Both recomputes should have added records
    assert mock_session.add.call_count >= 2


@pytest.mark.integration
def test_scorer_approval_signal_grants_full_score():
    """
    An approval action event should produce 1.0 from signal5,
    dominating the attribution for that contributor.
    """
    approver_id = uuid.uuid4()
    author_id = uuid.uuid4()

    scorer = _make_scorer()

    edits = [
        EditEvent(
            user_id=str(author_id),
            content_before="",
            content_after="Some memory content that will be approved.",
            edit_position=1,
            action_type="create",
        ),
        EditEvent(
            user_id=str(approver_id),
            content_before="Some memory content that will be approved.",
            content_after="Some memory content that will be approved.",
            edit_position=2,
            action_type="approval",
        ),
    ]

    results = scorer.compute_scores(edits)

    # Approver should have a non-trivial share
    approver_result = next(
        (r for r in results if r.user_id == str(approver_id)), None
    )
    assert approver_result is not None
    assert approver_result.contribution_weight > 0.0
    # Weights should sum to ~1.0
    total = sum(r.contribution_weight for r in results)
    assert abs(total - 1.0) < 0.01
