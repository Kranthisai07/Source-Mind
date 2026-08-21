"""The LLM must never pick a winner in conflict detection.

Its only job is to decide whether two memories are mutually exclusive claims
about the same decision point, and to describe that disagreement factually.
Resolution belongs to a human, and since Phase 1.5 only owners and admins may
act on it — which makes a ranked recommendation shown to that person an anchor
rather than advice.

TWO KINDS OF CONFIDENCE, only one acceptable:

  detection   "how sure am I these concern the same point"  -> fine
  correctness "how sure am I that A is the right answer"    -> banned

The prompts are the surface under test here, because that is where the
instruction to rank either exists or does not.

Phase 2.5 went further and deleted the resolution-time advisory outright
(ADR-010, superseding ADR-008). OLD_ADVISORY_PROMPT below is retained as
a fixture: it is what the checks must still be able to catch.
"""

from __future__ import annotations

import inspect
import re

import pytest

from sourcemind.schemas.conflict import ConflictDetail
from sourcemind.services.conflict import resolver
from sourcemind.services.memory import relations

# Phrases that ask the model to rank, prefer, or decide between the two claims.
WINNER_PHRASES = [
    "which is more",
    "more specific",
    "better supported",
    "evidence-based",
    "most likely",
    "best resolution",
    "suggest the best",
    "becomes outdated",
    "contradicts or replaces",
    "recommended",
    "kept_a",
    "kept_b",
    "suggested_resolution",
]

# A sentence that FORBIDS something may legitimately name it. A prohibition is
# not an instruction, so those sentences are excluded before scanning.
PROHIBITION = re.compile(r"\b(do not|don't|never|is not|must not)\b", re.IGNORECASE)


def _instructional_text(prompt: str) -> str:
    """The prompt minus its prohibition sentences.

    The unit has to be the sentence, not the line. These prompts are written as
    adjacent string literals, so a single sentence wraps across several source
    lines: "Do NOT judge which statement is" ends one line and "correct, better
    supported..." begins the next. Scanning line by line reads that second half
    as a standing instruction to rank.

    Sentence boundaries only exist once the literal seams are gone, because the
    quote character sits between the full stop and the space that follows it.
    So quotes and escaped newlines are flattened to whitespace first.
    """
    flat = prompt.replace("\\n", " ").replace('"', " ").replace("'", " ")
    flat = " ".join(flat.split())
    sentences = re.split(r"(?<=[.?!])\s+", flat)
    return " ".join(s for s in sentences if not PROHIBITION.search(s))


# The pre-Phase-2 prompts, kept verbatim so the tests below can be shown to
# actually catch them. A guard that cannot fail proves nothing.
OLD_CLASSIFY_PROMPT = (
    "Classify the relationship between these two statements.\n"
    "Choose exactly one:\n"
    '- "updates": B contradicts or replaces A (A becomes outdated)\n'
    '- "extends": B adds new detail to A (both remain valid)\n'
    'Respond in JSON only: {"relation": string, "confidence": float, '
    '"reasoning": string}'
)
OLD_ADVISORY_PROMPT = (
    "Two statements in a team knowledge base are in conflict.\n"
    "Analyze them and suggest the best resolution.\n"
    "Consider:\n"
    "- Which is more specific and evidence-based?\n"
    'Respond in JSON:\n{"suggested_resolution": "kept_a|kept_b|merged|split",\n'
    ' "confidence": 0.0-1.0}'
)


def _found(prompt: str) -> list[str]:
    text = _instructional_text(prompt).lower()
    return [p for p in WINNER_PHRASES if p in text]


# ─── the guards are not vacuous ──────────────────────────────────────────────

@pytest.mark.unit
def test_the_winner_language_check_catches_the_old_classification_prompt():
    """Fed the pre-Phase-2 prompt, the check must fire.

    Without this, a passing test on the new prompt would prove only that the
    phrase list happens not to match anything.
    """
    hits = _found(OLD_CLASSIFY_PROMPT)
    assert "becomes outdated" in hits
    assert "contradicts or replaces" in hits


@pytest.mark.unit
def test_the_winner_language_check_catches_the_old_advisory_prompt():
    hits = _found(OLD_ADVISORY_PROMPT)
    assert "suggest the best" in hits
    assert "more specific" in hits
    assert "kept_a" in hits


@pytest.mark.unit
def test_prohibitions_are_not_mistaken_for_instructions():
    """Naming a banned behaviour in order to forbid it must not trip the check."""
    assert _found("Do NOT judge which statement is better supported.") == []
    assert _found("Rank them by which is more specific."), (
        "an actual ranking instruction must still be caught"
    )


# ─── the current prompts ─────────────────────────────────────────────────────

@pytest.mark.unit
def test_conflict_prompt_contains_no_winner_language():
    """The live classification prompt must not ask the model to rank."""
    source = inspect.getsource(relations._classify_relation)
    hits = _found(source)
    assert not hits, f"winner-implying framing in the classification prompt: {hits}"


@pytest.mark.unit
def test_the_resolver_generates_no_ai_suggestion_at_all():
    """ADR-010: the advisory mechanism is removed, not reworded.

    Phase 2 made the advisory prompt neutral, which was not enough. Any
    model-written text on the resolution screen reads as a lead to the
    owner or admin who is the only one able to act on it, so the check
    is for absence of the machinery rather than for its wording.
    """
    source = inspect.getsource(resolver)
    assert "_SUGGESTION_PROMPT" not in source
    assert "_generate_suggestion" not in source

    # No model client can be handed to the read path.
    params = inspect.signature(resolver.get_conflict_detail).parameters
    assert "anthropic_client" not in params, (
        "get_conflict_detail must not accept a model client"
    )

    # And the field it populated is off the wire.
    assert "suggested_resolution" not in ConflictDetail.model_fields


@pytest.mark.unit
def test_conflict_response_schema_has_no_ranking_field():
    """No response field may encode a preference between the two claims."""
    banned_fields = [
        "suggested_resolution",
        "preferred",
        "more_likely",
        "confidence_a",
        "confidence_b",
        "winner",
        "recommended",
    ]
    prompt = inspect.getsource(relations._classify_relation)
    for field in banned_fields:
        assert field not in prompt, f"ranking field {field!r} in response schema"


@pytest.mark.unit
def test_classification_carries_only_neutral_conflict_fields():
    """is_conflict and conflict_summary — nothing that ranks the two."""
    fields = set(relations.Classification._fields)
    assert {"is_conflict", "conflict_summary"} <= fields
    assert not fields & {"preferred", "winner", "more_likely", "recommended"}


@pytest.mark.unit
def test_detection_confidence_is_documented_as_classification_certainty():
    """The surviving confidence must be about the classification, not truth.

    A bare 'confidence' next to a conflict verdict is read as confidence that
    one side is right, so the prompt has to say which it means.
    """
    source = inspect.getsource(relations._classify_relation)
    assert "NOT a judgement about which" in source, (
        "the prompt must state that confidence is not about correctness"
    )
    assert "classification is correct" in source


# ─── the conflict trigger itself ─────────────────────────────────────────────

@pytest.mark.unit
def test_conflicts_are_raised_on_mutual_exclusivity_not_supersession():
    """_maybe_create_conflict must key off is_conflict, not relation == updates.

    Keying off 'updates' meant every conflict in the system originated from a
    verdict whose own definition said one side was outdated.
    """
    source = inspect.getsource(relations._maybe_create_conflict)
    assert "verdict.is_conflict" in source
    assert 'relation_type != "updates"' not in source
