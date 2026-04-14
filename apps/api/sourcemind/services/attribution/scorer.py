"""
Attribution scorer — 5-signal weighted contribution algorithm.

Signals (weights):
  1. Character diff     0.35  — how much was changed relative to content length
  2. Semantic similarity 0.30  — how much of contributor's phrasing survived (SBERT)
  3. Temporal primacy   0.15  — first author benefits most (0.8^(pos-1))
  4. Structural         0.10  — how many new named entities were introduced
  5. Explicit approval  0.10  — action type indicates explicit review/approval

Signal 4 uses spaCy NER when available. On Python 3.14+ where spaCy is
incompatible, a regex fallback extracts technical identifiers (CamelCase,
ALLCAPS, version strings, known tech keywords). See ADR-007.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# ─── Signal 4: NER backend selection ─────────────────────────────────────────

try:
    import spacy as _spacy
    _spacy_nlp = _spacy.load("en_core_web_sm")
    SPACY_AVAILABLE = True
    log.info("ner.backend", backend="spacy", model="en_core_web_sm")
except Exception:
    _spacy_nlp = None
    SPACY_AVAILABLE = False
    log.info("ner.backend", backend="regex_fallback", reason="spacy_unavailable")

# Regex: CamelCase words | ALLCAPS (2+ chars) | version strings | known tech keywords
_TECH_PATTERN = re.compile(
    r'\b('
    r'[A-Z][a-z]+(?:[A-Z][a-z]+)+'          # CamelCase (e.g. PostgreSQL, GraphQL)
    r'|[A-Z]{2,}'                             # ALLCAPS (e.g. API, JWT, AWS, SQL)
    r'|v\d+(?:\.\d+)+'                        # version strings (e.g. v1.0, v2.3.1)
    r'|(?:OAuth|JWT|API|SQL|REST|gRPC'
    r'|S3|EC2|RDS|Redis|Kafka|Docker'
    r'|Kubernetes|PostgreSQL|Neo4j|Supabase'
    r'|Clerk|Celery|Alembic|FastAPI|Pydantic)'
    r')\b'
)

# Signal weights (must sum to 1.0)
_W1 = 0.35  # character diff
_W2 = 0.30  # semantic similarity
_W3 = 0.15  # temporal primacy
_W4 = 0.10  # structural (new named entities)
_W5 = 0.10  # explicit approval

# Floor: min share for any contributor with a substantive edit (>10 chars changed)
_FLOOR = 0.02
_SUBSTANTIVE_CHARS = 10

# Approval action types that grant full Signal 5 score
_APPROVAL_ACTIONS = frozenset({"approved", "merged", "accepted", "approve", "merge"})


@dataclass
class EditEvent:
    """
    Represents a single content edit by one contributor.

    Used as input to compute_scores().
    """
    user_id: str
    content_before: str | None       # None for initial creation
    content_after: str
    edit_position: int                # 1-indexed ordinal in the edit sequence
    action_type: str                  # AttributionActionType value


@dataclass
class ContributorScore:
    """Raw (un-normalized) per-contributor score from all their edits."""
    user_id: str
    raw_score: float = 0.0
    has_substantive_edit: bool = False

    # Signal breakdown (average across edits, for audit)
    char_diff_avg: float = 0.0
    semantic_avg: float = 0.0
    temporal_avg: float = 0.0
    structural_avg: float = 0.0
    approval_avg: float = 0.0
    edit_count: int = 0


@dataclass
class NormalizedAttribution:
    """Final normalized attribution for one contributor."""
    user_id: str
    contribution_weight: float        # [0.0–1.0], all contributors sum to 1.0
    char_diff_score: float | None
    semantic_score: float | None
    temporal_score: float | None
    structural_score: float | None
    approval_score: float | None


def _extract_entities(text: str) -> set[str]:
    """
    Extract named entities from text.

    Uses spaCy NER when available; falls back to regex-based extraction of
    technical identifiers (CamelCase, ALLCAPS, version strings, known tech terms)
    when spaCy is not installed or incompatible with the current Python version.
    """
    if SPACY_AVAILABLE and _spacy_nlp is not None:
        return {ent.text for ent in _spacy_nlp(text).ents}
    return set(_TECH_PATTERN.findall(text))


# ─── Signal computation ───────────────────────────────────────────────────────

def _signal1_char_diff(before: str | None, after: str) -> float:
    """
    Signal 1: Character-level edit distance as fraction of content length.

    score = 1 - (levenshtein(before, after) / max(len(before), len(after)))
    High score = large fraction of content was changed by this contributor.
    """
    if not before:
        # Initial creation: contributor changed 100%
        return 1.0
    try:
        from Levenshtein import distance as lev_distance
        dist = lev_distance(before, after)
        denom = max(len(before), len(after), 1)
        return 1.0 - (dist / denom)
    except Exception:
        # Fallback: simple char-level overlap
        common = sum(a == b for a, b in zip(before, after))
        denom = max(len(before), len(after), 1)
        return common / denom


def _signal2_semantic(contributor_input: str, final_content: str, sbert_model: Any) -> float:
    """
    Signal 2: SBERT cosine similarity between contributor's text and final content.

    Measures how much the contributor's phrasing survived into the final version.
    Model is passed in (loaded once at service startup — never reload per request).
    """
    try:
        import numpy as np
        embs = sbert_model.encode(
            [contributor_input, final_content],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return float(np.dot(embs[0], embs[1]))
    except Exception as exc:
        log.debug("signal2_failed", error=str(exc))
        return 0.5  # Neutral fallback


def _signal3_temporal(edit_position: int) -> float:
    """
    Signal 3: Temporal primacy — 0.8^(position-1).

    First author: 1.0, second: 0.8, third: 0.64, fourth: 0.512, etc.
    """
    return 0.8 ** (edit_position - 1)


def _signal4_structural(before: str | None, after: str) -> float:
    """
    Signal 4: Fraction of entities in 'after' that were NOT in 'before'.

    Rewards contributors who introduced new named concepts.
    Uses spaCy NER when available; regex fallback otherwise.
    """
    try:
        entities_after = _extract_entities(after)
        if not entities_after:
            return 0.0
        entities_before = _extract_entities(before) if before else set()
        new_entities = entities_after - entities_before
        return len(new_entities) / len(entities_after)
    except Exception as exc:
        log.debug("signal4_failed", error=str(exc))
        return 0.0


def _signal5_approval(action_type: str) -> float:
    """
    Signal 5: Explicit approval. Only specific action types count.
    """
    return 1.0 if action_type.lower() in _APPROVAL_ACTIONS else 0.0


# ─── Main scorer ─────────────────────────────────────────────────────────────

class AttributionScorer:
    """
    Computes normalized attribution weights for all contributors to a memory.

    Models are loaded once at construction — never reloaded per call.
    Pass the same scorer instance across requests for efficiency.
    """

    def __init__(self) -> None:
        self._sbert: Any = None

    def _get_sbert(self) -> Any:
        if self._sbert is None:
            from sentence_transformers import SentenceTransformer
            self._sbert = SentenceTransformer("all-MiniLM-L6-v2")
        return self._sbert

    def compute_scores(self, edits: list[EditEvent]) -> list[NormalizedAttribution]:
        """
        Compute normalized attribution for a list of edit events.

        Returns one NormalizedAttribution per unique contributor,
        with weights summing to 1.0.
        """
        if not edits:
            return []

        sbert = self._get_sbert()

        contributor_map: dict[str, ContributorScore] = {}

        for edit in edits:
            uid = edit.user_id
            if uid not in contributor_map:
                contributor_map[uid] = ContributorScore(user_id=uid)

            cs = contributor_map[uid]

            s1 = _signal1_char_diff(edit.content_before, edit.content_after)
            s2 = _signal2_semantic(
                edit.content_after if not edit.content_before else edit.content_before,
                edit.content_after,
                sbert,
            )
            s3 = _signal3_temporal(edit.edit_position)
            s4 = _signal4_structural(edit.content_before, edit.content_after)
            s5 = _signal5_approval(edit.action_type)

            weighted = s1 * _W1 + s2 * _W2 + s3 * _W3 + s4 * _W4 + s5 * _W5

            cs.raw_score += weighted
            cs.edit_count += 1

            # Running average of signal breakdowns (for audit)
            n = cs.edit_count
            cs.char_diff_avg = cs.char_diff_avg * (n - 1) / n + s1 / n
            cs.semantic_avg = cs.semantic_avg * (n - 1) / n + s2 / n
            cs.temporal_avg = cs.temporal_avg * (n - 1) / n + s3 / n
            cs.structural_avg = cs.structural_avg * (n - 1) / n + s4 / n
            cs.approval_avg = cs.approval_avg * (n - 1) / n + s5 / n

            # Check substantive edit
            before_len = len(edit.content_before or "")
            after_len = len(edit.content_after)
            changed_chars = abs(after_len - before_len)
            if changed_chars >= _SUBSTANTIVE_CHARS:
                cs.has_substantive_edit = True

        return self._normalize(list(contributor_map.values()))

    def _normalize(self, contributors: list[ContributorScore]) -> list[NormalizedAttribution]:
        """
        Normalize raw scores to sum to 1.0, applying the floor for substantive editors.
        """
        if not contributors:
            return []

        total = sum(c.raw_score for c in contributors)
        if total == 0:
            # Edge case: all signals zero — give equal weight
            equal = 1.0 / len(contributors)
            return [
                NormalizedAttribution(
                    user_id=c.user_id,
                    contribution_weight=equal,
                    char_diff_score=None,
                    semantic_score=None,
                    temporal_score=None,
                    structural_score=None,
                    approval_score=None,
                )
                for c in contributors
            ]

        # Initial normalization
        normalized = {c.user_id: c.raw_score / total for c in contributors}

        # Apply floor
        floor_applied = {
            c.user_id: c.has_substantive_edit
            for c in contributors
        }
        changed = True
        while changed:
            changed = False
            floored_ids = [uid for uid, is_sub in floor_applied.items()
                           if is_sub and normalized[uid] < _FLOOR]
            if not floored_ids:
                break
            # Set floored contributors to _FLOOR
            floored_total = len(floored_ids) * _FLOOR
            non_floored_total = sum(normalized[uid] for uid in normalized
                                    if uid not in floored_ids)
            if non_floored_total == 0:
                # All would be floored equally
                for uid in floored_ids:
                    normalized[uid] = _FLOOR
                break
            # Redistribute the deficit from non-floored contributors
            scale = (1.0 - floored_total) / non_floored_total
            for uid in floored_ids:
                normalized[uid] = _FLOOR
                changed = True
            for uid in normalized:
                if uid not in [f for f in floored_ids]:
                    normalized[uid] *= scale

        # Final renormalize to ensure exact 1.0
        final_total = sum(normalized.values())
        if final_total > 0:
            normalized = {uid: w / final_total for uid, w in normalized.items()}

        cs_map = {c.user_id: c for c in contributors}
        return [
            NormalizedAttribution(
                user_id=uid,
                contribution_weight=round(w, 6),
                char_diff_score=cs_map[uid].char_diff_avg,
                semantic_score=cs_map[uid].semantic_avg,
                temporal_score=cs_map[uid].temporal_avg,
                structural_score=cs_map[uid].structural_avg,
                approval_score=cs_map[uid].approval_avg,
            )
            for uid, w in normalized.items()
        ]


# Module-level singleton for route handlers (lazy-initialized)
_scorer_instance: AttributionScorer | None = None


def get_scorer() -> AttributionScorer:
    """Return the module-level AttributionScorer singleton."""
    global _scorer_instance
    if _scorer_instance is None:
        _scorer_instance = AttributionScorer()
    return _scorer_instance
