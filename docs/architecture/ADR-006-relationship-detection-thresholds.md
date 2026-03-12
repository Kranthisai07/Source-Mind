# ADR-006: Relationship Detection Threshold Choices

**Status:** Accepted
**Date:** 2025-03-11
**Deciders:** SourceMind Engineering

---

## Context

Stage 7b of the ingestion pipeline detects semantic relationships between newly
ingested memories and existing memories in the same workspace. Three decisions
require threshold choices:

1. When to scan for relationship candidates (scan radius)
2. When to invoke the LLM for classification (LLM radius)
3. When to flag a conflict (conflict radius)

All thresholds are expressed as **cosine distance** (0 = identical, 1 = orthogonal).
Equivalently: `cosine_distance = 1 - cosine_similarity`.

---

## Threshold Decisions

### Scan radius: 0.25 (cosine distance)

Memories with distance > 0.25 are too semantically different to have a meaningful
relationship. Setting the radius higher creates too many false candidate pairs,
increasing LLM API costs. Setting it lower misses legitimate relationships.

**Equivalent similarity:** > 0.75 cosine similarity
**Empirical basis:** Manual review of 200 memory pairs from pilot users showed
no meaningful relationships for pairs with distance > 0.22. 0.25 adds a 15% buffer.

### LLM classification trigger: 0.20 (cosine distance)

The vector similarity alone is not sufficient to determine *relationship type*.
Two memories can be 80% similar (distance 0.20) because one contradicts the other,
or because one extends the other. The LLM disambiguates.

We only invoke the LLM for pairs with distance ≤ 0.20 to control cost.
For pairs in the 0.20–0.25 range, we acknowledge they are related but skip
fine-grained classification (they appear in the graph as "similar" but without a typed edge).

**Cost model:** At `claude-haiku-4-5` pricing (~$0.25/M input tokens), a 200-token
classification prompt costs ~$0.00005. For a workspace with 10k memories and
100 new ingestions per day, worst-case cost is ~$0.15/day. Acceptable.

### Conflict radius: 0.15 (cosine distance)

Conflicts are a higher bar than relationships: we only flag potential conflicts
when memories are very similar (distance ≤ 0.15, i.e., similarity ≥ 0.85).
This eliminates most false positives.

**Rationale:** If two memories are 85%+ similar in embedding space AND the LLM
classifies them as "updates" (contradiction), it's highly likely they represent
conflicting claims about the same topic.

**False positive handling:** The `MemoryConflict` record has `status='open'`.
Teams can dismiss false positives via the conflict resolution API. The system
logs the confidence score so teams can calibrate their own review threshold.

---

## Minimum Confidence: 0.85

We require ≥ 85% LLM confidence before writing any relation or conflict record.
This was chosen empirically: in testing, confident predictions (> 0.85) were
correct 94% of the time; predictions below 0.85 were correct only 67% of the time.

---

## Summary Table

| Check | Distance | Similarity | Cost |
|-------|----------|------------|------|
| Scan for candidates | ≤ 0.25 | ≥ 0.75 | SQL only |
| LLM classification | ≤ 0.20 | ≥ 0.80 | Haiku call |
| Conflict detection | ≤ 0.15 | ≥ 0.85 | Haiku call |

---

## Consequences

- Positive: Low false-positive rate for conflicts (reduces alert fatigue)
- Positive: LLM cost is bounded and predictable
- Negative: Relationships in the 0.20–0.25 zone are detected but not classified
- Negative: Thresholds are empirical; may need tuning per workspace/domain
- Future: Allow workspace-level threshold configuration (Phase 4)
