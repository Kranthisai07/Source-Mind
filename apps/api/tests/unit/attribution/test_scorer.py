"""Unit tests for the 5-signal attribution scorer."""

from unittest.mock import MagicMock, patch

import pytest

from sourcemind.services.attribution.scorer import (
    AttributionScorer,
    EditEvent,
    NormalizedAttribution,
    _extract_entities,
    _signal1_char_diff,
    _signal3_temporal,
    _signal4_structural,
    _signal5_approval,
)


def _make_scorer(sbert_similarity: float = 0.9) -> AttributionScorer:
    """Create a scorer with mocked SBERT. Signal 4 uses module-level _extract_entities (no mock needed)."""
    scorer = AttributionScorer()

    # Mock SBERT
    sbert_mock = MagicMock()
    import numpy as np
    v1 = np.array([sbert_similarity, 0.0])
    v2 = np.array([1.0, 0.0])
    sbert_mock.encode = MagicMock(return_value=np.array([v1, v2]))
    scorer._sbert = sbert_mock

    return scorer


# ─── Signal unit tests ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_signal1_character_diff_full_replacement_scores_low():
    """Replacing almost nothing gives a low char-diff score."""
    before = "A" * 100
    after = "B" * 100
    score = _signal1_char_diff(before, after)
    assert score < 0.1, f"Expected low score for full replacement, got {score}"


@pytest.mark.unit
def test_signal1_character_diff_minor_edit_scores_high():
    """Changing one word in a long string gives a high char-diff score."""
    before = "The system uses PostgreSQL 16 for storage."
    after = "The system uses PostgreSQL 17 for storage."
    score = _signal1_char_diff(before, after)
    assert score > 0.9, f"Expected high score for minor edit, got {score}"


@pytest.mark.unit
def test_signal2_semantic_same_content_scores_high():
    """SBERT similarity for identical texts approaches 1.0."""
    scorer = _make_scorer(sbert_similarity=1.0)
    sbert = scorer._get_sbert()
    from sourcemind.services.attribution.scorer import _signal2_semantic
    score = _signal2_semantic("identical text", "identical text", sbert)
    assert score >= 0.9


@pytest.mark.unit
def test_signal3_temporal_first_author_scores_1():
    assert _signal3_temporal(1) == 1.0


@pytest.mark.unit
def test_signal3_temporal_fourth_author_scores_point_51():
    score = _signal3_temporal(4)
    assert abs(score - 0.8**3) < 0.001  # 0.8^3 = 0.512


@pytest.mark.unit
def test_signal4_structural_new_entities_increase_score():
    """New entities introduced by a contributor should yield a positive score."""
    score = _signal4_structural(None, "AWS deployed Redis and Kafka to PostgreSQL cluster")
    assert score > 0


@pytest.mark.unit
def test_signal4_structural_no_new_entities_scores_zero():
    """When 'after' introduces no new entities beyond 'before', score is 0.0."""
    text = "AWS deployed Redis and Kafka"
    score = _signal4_structural(text, text)
    assert score == 0.0


@pytest.mark.unit
def test_signal4_extract_entities_camel_case():
    """CamelCase words should be recognized as entities."""
    entities = _extract_entities("We migrated from GraphQL to PostgreSQL using FastAPI")
    assert "GraphQL" in entities or "PostgreSQL" in entities or "FastAPI" in entities


@pytest.mark.unit
def test_signal4_extract_entities_allcaps():
    """ALLCAPS abbreviations should be recognized as entities."""
    entities = _extract_entities("The JWT token is validated via OAuth using the API")
    assert any(e in entities for e in ("JWT", "OAuth", "API"))


@pytest.mark.unit
def test_signal4_extract_entities_version_string():
    """Version strings like v2.3.1 should be recognized."""
    entities = _extract_entities("Upgraded to v2.3.1 from v1.0")
    assert any("v2" in e or "v1" in e for e in entities)


@pytest.mark.unit
def test_signal5_approval_action_grants_fixed_score():
    assert _signal5_approval("approved") == 1.0
    assert _signal5_approval("merged") == 1.0
    assert _signal5_approval("edit") == 0.0
    assert _signal5_approval("create") == 0.0


# ─── Aggregation tests ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_aggregation_normalizes_to_1_point_0():
    """All contributor weights must sum to exactly 1.0."""
    scorer = _make_scorer()
    edits = [
        EditEvent(user_id="user_a", content_before=None, content_after="First version.", edit_position=1, action_type="create"),
        EditEvent(user_id="user_b", content_before="First version.", content_after="Second version.", edit_position=2, action_type="edit"),
        EditEvent(user_id="user_c", content_before="Second version.", content_after="Third version.", edit_position=3, action_type="edit"),
    ]
    results = scorer.compute_scores(edits)
    total = sum(r.contribution_weight for r in results)
    assert abs(total - 1.0) < 1e-5, f"Weights sum to {total}, not 1.0"


@pytest.mark.unit
def test_floor_applied_for_minor_contributors():
    """
    A contributor with a substantive edit (>10 chars changed) but very low
    raw score should be floored to 0.02.
    """
    scorer = _make_scorer(sbert_similarity=0.01)  # low semantic score
    # user_a does a massive initial contribution
    # user_b makes a tiny change
    edits = [
        EditEvent(
            user_id="user_a",
            content_before=None,
            content_after="A" * 500,
            edit_position=1,
            action_type="create",
        ),
        EditEvent(
            user_id="user_a",
            content_before="A" * 500,
            content_after="A" * 500,
            edit_position=2,
            action_type="edit",
        ),
        EditEvent(
            user_id="user_a",
            content_before="A" * 500,
            content_after="A" * 500,
            edit_position=3,
            action_type="edit",
        ),
        EditEvent(
            user_id="user_a",
            content_before="A" * 500,
            content_after="A" * 500,
            edit_position=4,
            action_type="edit",
        ),
        EditEvent(
            user_id="user_b",
            content_before="A" * 500,
            content_after="A" * 500 + "x" * 15,  # substantive edit (15 chars added)
            edit_position=5,
            action_type="edit",
        ),
    ]
    results = scorer.compute_scores(edits)
    user_b_result = next((r for r in results if r.user_id == "user_b"), None)
    assert user_b_result is not None
    assert user_b_result.contribution_weight >= 0.02 - 1e-9


@pytest.mark.unit
def test_single_contributor_gets_full_attribution():
    """When there's only one contributor, they get weight=1.0."""
    scorer = _make_scorer()
    edits = [
        EditEvent(
            user_id="solo_user",
            content_before=None,
            content_after="The system uses Redis for caching.",
            edit_position=1,
            action_type="create",
        ),
    ]
    results = scorer.compute_scores(edits)
    assert len(results) == 1
    assert abs(results[0].contribution_weight - 1.0) < 1e-5
