# ADR-009: Knowledge Handoff — Tier Classification Thresholds

**Status:** Accepted
**Date:** 2026-03-12
**Authors:** SourceMind Core Team

---

## Context

When a contributor leaves a workspace (resignation, team change, role transition), their knowledge must be transferred. Without a structured handoff, critical information becomes orphaned — no one knows it exists, and no one maintains it.

The challenge is prioritization: a departing contributor may have attribution on hundreds of memories. Not all require immediate attention. We need a principled way to classify which memories are *critical*, *important*, or *can wait*.

---

## Decision

Memories are classified into three tiers based on two signals: **importance score** and **contributor concentration**.

### Tier Definitions

| Tier | Name | Condition | Action Required |
|------|------|-----------|-----------------|
| 1 | Critical | `importance_score > 0.8` AND departing user is effectively the sole significant contributor (`no other contributor has > 30% share`) | Must be assigned before handoff completes |
| 2 | Important | `importance_score > 0.5` AND not Tier 1 | Should be assigned; surfaced in UI with amber status |
| 3 | Standard | All other memories with any attribution | Noted in summary; no blocking action required |

### Threshold Rationale

**`importance_score > 0.8` for Tier 1:**
The `importance_score` field on memories (0.0–1.0) is set during ingestion based on signals including: number of inbound memory relations, explicitly high-confidence facts, and tagging as decisions/incidents/runbooks. 0.8 was chosen as the threshold for "this memory is architecturally significant" — above this, losing the sole owner represents genuine knowledge risk.

**Solo contributor threshold `> 30%`:**
If another contributor holds more than 30% of the attribution weight, they have meaningful co-ownership and can maintain the memory without a formal handoff. Below 30%, the departing user's knowledge is not reliably shared with anyone remaining on the team.

**`importance_score > 0.5` for Tier 2:**
The midpoint of the scale. Memories above 0.5 are "above average importance" — not critical infrastructure knowledge, but worth a warm handover rather than cold orphaning.

### Attribution Transfer Rule

When a handoff assignment is confirmed (`POST /v1/workspaces/:id/handoff/assign`):
- The new owner receives **40% of the departing user's current attribution weight**
- This is inserted as a new `Attribution` record (append-only)
- The departing user's historical records are never modified

The 40% figure reflects that the new owner is *starting* to own the memory, not inheriting full expertise. They will build attribution naturally through edits over time.

### Handoff Window

The handoff process has a **30-day window** (`_HANDOFF_WINDOW_DAYS = 30`). After this period, the `handoff_records` row is flagged as expired. Unassigned Tier 1 memories are surfaced in workspace analytics as knowledge gaps.

---

## Alternatives Considered

### Option A: All memories treated equally
- **Rejected**: Creates unmanageable workload. A contributor with 500 memories cannot do a thorough handoff in one sitting. Triage is essential.

### Option B: Single threshold (critical only, no Tier 2)
- **Rejected**: Important-but-not-critical memories are still worth flagging. Tier 2 provides a "do soon" category without blocking the departure.

### Option C: AI-generated importance scores at handoff time
- **Rejected**: Adds latency and non-determinism to the handoff initiation endpoint. `importance_score` is already persisted on the Memory row and updated continuously by the ingestion pipeline.

### Option D: Full ownership transfer (100% attribution shift)
- **Rejected**: Misrepresents the intellectual history. The departing user genuinely contributed the memory; their attribution should remain in the historical record. The 40% transfer accurately represents "this person is now the primary custodian going forward."

### Option E: Manager-assigned thresholds per workspace
- **Deferred**: Good future feature (different teams have different criticality definitions). Phase 3 uses global defaults. Per-workspace threshold overrides can be added to the `workspaces` table without a schema redesign.

---

## Consequences

**Positive:**
- Clear, actionable output: Tier 1 memories require immediate action before status='departed'
- Successor suggestions reduce the friction of finding the right new owner
- 40% transfer rule preserves intellectual history while establishing new custodianship
- The 30-day window creates urgency without forcing rushed decisions

**Negative:**
- `importance_score` must be accurately maintained by the ingestion pipeline for tier classification to be meaningful — garbage in, garbage out
- The 30% solo-contributor threshold may miss memories where the departing user has 25% but others are spread across 5 people (no clear single successor)
- Tier classification is point-in-time at handoff initiation; edits after `classify_memories()` is called are not reflected

**Neutral:**
- The handoff workflow is triggered manually by a workspace admin, not automatically on account deactivation
- Tier 3 memories are counted but not actioned — this is intentional; over-alerting causes alert fatigue

---

## Implementation

- `sourcemind/services/attribution/handoff.py` — `classify_memories()`, `create_handoff_record()`, `assign_memory()`, `complete_handoff()`
- `sourcemind/api/v1/team.py` — `handoff_router` with three endpoints
- `alembic/versions/20250312_0002_phase3_additions.py` — `handoff_records`, `handoff_assignments` tables; `workspace_members.status` column
- `tests/unit/attribution/test_handoff.py` — 6 unit tests
- `tests/integration/test_handoff_workflow.py` — full lifecycle integration test

### Constants (from `handoff.py`)

```python
_TIER_1_IMPORTANCE = 0.8        # importance_score threshold for critical
_TIER_2_IMPORTANCE = 0.5        # importance_score threshold for important
_TIER_1_SOLO_THRESHOLD = 0.3    # max attribution share for "sole contributor"
_HANDOFF_WINDOW_DAYS = 30       # days before handoff record expires
```
