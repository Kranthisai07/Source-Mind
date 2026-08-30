"""First-creation attribution is computed, not asserted.

create_initial_attribution used to write (1.0, 1.0, 1.0, 1.0, 0.0) by hand.
That was a hardcoded duplicate of what the scorer produces for a first
creation, not a placeholder standing in for it — so wiring the scorer in
changed exactly one value, structural_score, which was wrong for entity-free
content.

WHY FOUR SIGNALS ARE CONSTANT HERE, AND WHY THAT IS CORRECT
-----------------------------------------------------------
The algorithm measures RELATIVE contribution among several contributors across
an edit history. A first creation is the degenerate case: one contributor, one
edit, nothing to compare against. The constants are the right answer to that
case, not evidence of a stub. These tests pin the reasoning down so a future
reader does not "fix" a constant that is supposed to be constant.

The test that actually exercises the algorithm is the last one, where a second
contributor edits and the weights genuinely split.
"""

from __future__ import annotations

import pytest

from sourcemind.services.attribution.scorer import EditEvent, get_scorer

ENTITY_RICH = "We migrated the API from PostgreSQL to Neo4j using Kubernetes in v2.1."
ENTITY_FREE = "the team discussed it yesterday and agreed to move on quietly"


def _creation(content: str, user: str = "creator") -> EditEvent:
    return EditEvent(
        user_id=user,
        content_before=None,
        content_after=content,
        edit_position=1,
        action_type="create",
    )


@pytest.mark.unit
def test_first_creation_s1_s2_s3_are_constant_by_design():
    """Signals 1-3 are 1.0 for any first creation, for structural reasons.

    S1: _signal1_char_diff returns early when `before` is empty or None —
        there is nothing to diff against, so the creator changed 100% of the
        content. Levenshtein never runs, and passing "" instead of None hits
        the same branch.
    S2: compute_scores compares every contribution against the LATEST version,
        `edits[-1].content_after`. With a single edit that is the same string,
        so the cosine is 1.0 by identity, not by measurement.
    S3: 0.8^(position-1) with position 1. This genuinely is the first edit.

    Two very different documents are used to show the constancy is a property
    of the first-creation case rather than of one particular input.
    """
    scorer = get_scorer()
    for content in (ENTITY_RICH, ENTITY_FREE):
        result = scorer.compute_scores([_creation(content)])[0]
        assert result.char_diff_score == pytest.approx(1.0), content
        assert result.semantic_score == pytest.approx(1.0, abs=1e-4), content
        assert result.temporal_score == pytest.approx(1.0), content
        # Creating is not approving.
        assert result.approval_score == pytest.approx(0.0), content


@pytest.mark.unit
def test_first_creation_s4_varies_with_entity_content():
    """Signal 4 is the only one that discriminates on a first creation.

    Every entity is new when there is no `before`, so the score is 1.0 when the
    NER backend finds any entity and 0.0 when the content has none. The old
    hardcoded 1.0 was simply wrong for entity-free text — the one value this
    change actually corrects.
    """
    scorer = get_scorer()
    rich = scorer.compute_scores([_creation(ENTITY_RICH)])[0]
    plain = scorer.compute_scores([_creation(ENTITY_FREE)])[0]

    assert rich.structural_score == pytest.approx(1.0)
    assert plain.structural_score == pytest.approx(0.0)
    assert rich.structural_score != plain.structural_score, (
        "structural_score must reflect the content; if these match, the "
        "hardcoded constant is back"
    )


@pytest.mark.unit
def test_single_contributor_normalizes_to_full_weight():
    """One contributor takes 100%, whatever the raw signals say.

    Normalisation divides each contributor's raw score by the total. With one
    contributor those are the same number, so the weight is 1.0 even for
    entity-free content whose structural signal is 0.0.
    """
    scorer = get_scorer()
    for content in (ENTITY_RICH, ENTITY_FREE):
        results = scorer.compute_scores([_creation(content)])
        assert len(results) == 1
        assert results[0].contribution_weight == pytest.approx(1.0), content


@pytest.mark.unit
def test_second_contributor_edit_produces_real_split_weights():
    """The case the algorithm exists for: two contributors, real split.

    Everything above describes a degenerate case. Here there is an actual edit
    history, so the signals differ per contributor and the weights carry
    information:

      the creator  keeps S1 = 1.0 but loses semantic ground, because their
                   wording is now compared against someone else's final text
      the editor   earns a real S1 from the character diff, scores S2 = 1.0
                   against the final version they wrote, and takes a temporal
                   penalty of 0.8 for arriving second

    The assertion is deliberately not a specific pair of numbers - those track
    the weights in ADR-007 and would make this a change-detector. What matters
    is that the split is real: two contributors, weights summing to 1.0,
    neither at 0 or 1, and not a trivial 50/50.
    """
    creator, editor = "author-a", "author-b"
    original = "The API rate limit is 100 requests per second."
    revised = (
        "The API rate limit is 500 requests per second for authenticated "
        "clients, enforced by Redis."
    )

    results = get_scorer().compute_scores(
        [
            EditEvent(
                user_id=creator,
                content_before=None,
                content_after=original,
                edit_position=1,
                action_type="create",
            ),
            EditEvent(
                user_id=editor,
                content_before=original,
                content_after=revised,
                edit_position=2,
                action_type="edit",
            ),
        ]
    )

    assert len(results) == 2, "both contributors must appear"
    weights = {r.user_id: r.contribution_weight for r in results}
    assert sum(weights.values()) == pytest.approx(1.0), "weights must sum to 1.0"

    for user, weight in weights.items():
        assert 0.0 < weight < 1.0, (
            f"{user} took {weight}: one contributor absorbed everything, so "
            "nothing was actually discriminated"
        )

    assert weights[creator] != pytest.approx(weights[editor], abs=1e-3), (
        "an even split means the signals contributed nothing — the two edits "
        "differ in character diff, semantic survival and recency"
    )

    # And the per-signal values must differ too, not just the final weights.
    by_user = {r.user_id: r for r in results}
    assert by_user[creator].temporal_score > by_user[editor].temporal_score, (
        "temporal primacy must favour the first author"
    )
    assert by_user[editor].semantic_score > by_user[creator].semantic_score, (
        "the editor wrote the final text, so more of their phrasing survives"
    )
