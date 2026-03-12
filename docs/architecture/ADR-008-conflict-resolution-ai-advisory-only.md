# ADR-008: Conflict Resolution — AI Advisory Only

**Status:** Accepted
**Date:** 2026-03-12
**Authors:** SourceMind Core Team

---

## Context

SourceMind detects contradictory memories (e.g., "The API rate limit is 100 req/s" vs. "The API rate limit is 500 req/s") using the conflict detector from Phase 2 (`services/memory/relations.py`). Once detected, conflicts need to be *resolved* — but the question is: how much should the AI be trusted to resolve them automatically?

Key constraints:
- Some conflicts are genuine ambiguity (A is right in one context, B in another)
- Some are stale information (A was correct in 2023, B is correct now)
- Some are outright errors by contributors
- Only humans have the context to know which case applies

---

## Decision

AI (Claude Haiku) provides a **non-binding suggestion** only. All resolution decisions are made by a human team member.

### AI Role

When a conflict is first fetched via `GET /v1/conflicts/:id`, the system optionally generates an AI suggestion using Claude Haiku:

```
Given two conflicting memories, suggest the most likely resolution.
Be concise (2-3 sentences). Do not make the decision — advise.
```

The suggestion is stored in `memory_conflicts.suggested_resolution` (JSONB) and never regenerated unless the field is null. This is **one-shot generation with caching** — no repeated API calls.

### Resolution Types

Humans choose from five resolution actions:

| Type | Action |
|------|--------|
| `kept_a` | Memory A is correct; Memory B is deprecated (`current_version=FALSE`) |
| `kept_b` | Memory B is correct; Memory A is deprecated |
| `merged` | Both are partially correct; a new merged Memory is created, both originals deprecated |
| `split` | The conflict is a false positive; memories address different contexts (tagged to distinguish) |
| `deferred` | Unresolvable now; set `revisit_at` date for future review |

### Lifecycle

```
open → under_review → resolved
                   ↘ deferred (re-enters under_review later)
```

`mark_under_review()` requires the conflict to currently be `open` (atomic UPDATE with WHERE status='open'). This prevents race conditions from concurrent reviewers.

---

## Alternatives Considered

### Option A: Fully automated AI resolution
- **Rejected**: AI hallucination risk is unacceptable for factual knowledge bases. An incorrect auto-resolution would silently delete valid information. Human sign-off is required.

### Option B: No AI involvement
- **Rejected**: Reviewers benefit from a neutral starting point. The suggestion reduces cognitive load without removing human judgment. Claude Haiku is cheap and fast enough to run on-demand.

### Option C: AI confidence score → auto-resolve if high confidence
- **Rejected**: LLM confidence scores are poorly calibrated. A model that is "95% confident" can still be confidently wrong. We do not use self-reported confidence for auto-resolution.

### Option D: Majority vote from team members
- **Rejected**: Adds coordination overhead. For factual conflicts (not opinions), majority vote is not meaningfully more accurate than one knowledgeable reviewer.

---

## Consequences

**Positive:**
- Zero risk of AI silently corrupting the knowledge base
- Suggestion reduces time-to-resolution for clear-cut conflicts
- `deferred` state cleanly handles genuinely unresolvable conflicts without blocking the queue
- Resolution history is preserved (all conflict records kept with timestamps and resolver IDs)

**Negative:**
- Requires human review time; conflicts will queue up if team members are busy
- Claude Haiku API call on first view of a conflict adds ~300-500ms latency
- AI suggestion stored in JSONB is schema-free; future structured suggestions would require migration

**Neutral:**
- The AI suggestion is shown as advisory in the UI — reviewers are not anchored to it
- Deferred conflicts are not auto-escalated (out of scope for Phase 3; future work)

---

## Implementation

- `sourcemind/services/conflict/resolver.py` — `get_conflict_detail()`, `mark_under_review()`, `resolve_conflict()`
- `sourcemind/api/v1/conflicts.py` — four endpoints
- `sourcemind/models/memory_conflict.py` — `ConflictStatus` enum (open, under_review, resolved, deferred)
- `alembic/versions/20250312_0002_phase3_additions.py` — `reviewed_by`, `reviewed_at`, `suggested_resolution`, `revisit_at` columns
- `tests/unit/conflict/test_resolver.py` — 6 unit tests
- `tests/integration/test_conflict_resolution.py` — 2 integration tests
