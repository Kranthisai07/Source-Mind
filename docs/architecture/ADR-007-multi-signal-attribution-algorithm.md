# ADR-007: Multi-Signal Attribution Algorithm

**Status:** Accepted
**Date:** 2026-03-12
**Authors:** SourceMind Core Team

---

## Context

SourceMind tracks *who contributed what knowledge* to each memory in a workspace. Attribution must handle:

- Multiple contributors editing the same memory over time
- Small grammatical edits vs. substantial new information
- Original authors being credited even after many revisions
- Explicit approval actions (manager sign-off, expert review)
- Structural contributions like adding named entities, links, or code blocks

Simple "last editor wins" is wrong. Pure character-diff overweights trivial reformatting. We need a multi-signal model that approximates human judgment about intellectual contribution.

---

## Decision

We use a **5-signal weighted attribution model** computed over the full edit history of each memory version. The algorithm runs in `services/attribution/scorer.py`.

### Signals and Weights

| Signal | Weight | Measure |
|--------|--------|---------|
| S1: Character Diff | 35% | Levenshtein edit distance normalized by max content length |
| S2: Semantic Similarity | 30% | SBERT cosine distance (how much the meaning changed) |
| S3: Temporal Primacy | 15% | Exponential decay: `0.8^(edit_position - 1)` |
| S4: Structural Contribution | 10% | spaCy NER — new named entities introduced by this edit |
| S5: Explicit Approval | 10% | 1.0 if `action_type == "approval"`, else 0.0 |

**Total weight per contributor:**
```
raw_score = 0.35*S1 + 0.30*S2 + 0.15*S3 + 0.10*S4 + 0.10*S5
```

### Normalization

After computing raw scores across all contributors:
1. Scores are normalized to sum to 1.0
2. Any contributor with at least one *substantive edit* (>10 characters changed) receives a minimum floor of **0.02** (2%)
3. Floor application is iterative until stable (prevents cascading undershoot)

### Data Model

Attribution records are **append-only** (enforced by PostgreSQL trigger `attribution_append_only`, established in ADR-002). Every edit creates a new `AttributionEdit` + new `Attribution` rows. Historical records are never modified.

### Models Used

- **SBERT**: `sentence-transformers/all-MiniLM-L6-v2` — lightweight, fast, no GPU required
- **spaCy**: `en_core_web_sm` — NER pipeline for structural contribution detection
- Both are lazy-loaded on first use and cached as module-level singletons (`get_scorer()`)

---

## Alternatives Considered

### Option A: Pure character diff
- **Rejected**: Overweights trivial whitespace/punctuation edits; underweights high-value semantic contributions like adding a correct citation.

### Option B: LLM-based attribution scoring
- **Rejected**: Adds latency (50-200ms per attribution call), cost, and non-determinism. Attribution is called on every PATCH — must be synchronous and cheap.

### Option C: Time-based decay only
- **Rejected**: First contributor would dominate forever regardless of subsequent improvements. Does not handle the "someone else built on my sketch and made it great" pattern.

### Option D: Crowd-sourced voting
- **Rejected**: Requires active participation from team members; too high a friction cost for attribution on routine edits.

---

## Consequences

**Positive:**
- Attribution reflects genuine intellectual contribution, not just edit volume
- Approval signal lets managers and domain experts receive appropriate credit for validation work
- Floor prevents contributors from being erased by many small edits from others
- Deterministic given the same edit history (no randomness, no LLM non-determinism)

**Negative:**
- Requires `sentence-transformers` and `spaCy` as runtime dependencies (~200MB models)
- SBERT similarity is computed pairwise over all edit pairs — O(n²) for long edit histories (mitigated by processing only adjacent pairs in practice)
- spaCy `en_core_web_sm` is English-only; multilingual workspaces will need `xx_ent_wiki_sm`

**Neutral:**
- Attribution scores are advisory, not contractual — users see percentages, not legally binding records
- Models run in Celery worker processes, keeping API latency unaffected

---

## Implementation

- `sourcemind/services/attribution/scorer.py` — full 5-signal implementation
- `sourcemind/services/attribution/engine.py` — `recompute_attribution()` orchestrates scorer + DB writes
- `sourcemind/models/attribution.py` — `Attribution`, `AttributionEdit` ORM models
- `alembic/versions/20250312_0002_phase3_additions.py` — schema additions
- `tests/unit/attribution/test_scorer.py` — 10 unit tests covering all signals and edge cases
