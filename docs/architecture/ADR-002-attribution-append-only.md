# ADR-002: Attribution Records Are Append-Only (DB Trigger Enforced)

**Status:** Accepted
**Date:** 2025-03-11
**Deciders:** Principal Architect

---

## Context

Attribution data represents who owns what share of institutional knowledge. This data must be:
1. **Tamper-proof** — once computed, attribution cannot be retroactively altered
2. **Auditable** — every attribution change must have a clear reason (which edit triggered it)
3. **Historical** — the full attribution timeline must be queryable for compliance

## Decision

The `attributions` table is **append-only**, enforced by a PostgreSQL trigger `attribution_append_only` that prevents any `UPDATE` or `DELETE` operation.

```sql
CREATE TRIGGER attribution_append_only
BEFORE UPDATE OR DELETE ON attributions
FOR EACH ROW EXECUTE FUNCTION prevent_attribution_mutation();
```

This is enforced at the **database level**, not just the application level. Even a database administrator cannot modify records through normal SQL — they would need to drop the trigger first, creating a detectable audit trail.

## Rationale

**Why not just enforce this in application code?**
- Application-level enforcement can be bypassed by direct database access, future developers, or bugs
- Database-level enforcement is the only true guarantee
- This pattern is standard for financial ledgers and compliance-grade audit trails

**Why store snapshots rather than deltas?**
- Querying "current attribution" requires only `SELECT ... WHERE created_at = MAX(created_at) GROUP BY memory_id, user_id`
- No need to replay a full event log to get current state
- Trade-off: more storage, simpler queries

**What triggers a new attribution record?**
- Any `PATCH /v1/memories/:id` (content edit) — triggers 5-signal recompute
- Explicit approval action (adds 0.1 to approver's approval_score)
- Initial creation (submitter gets 100%)

## Consequences

**Positive:**
- Mathematical certainty that historical attribution is preserved
- Meets enterprise compliance requirements for knowledge provenance
- Cannot be accidentally corrupted by buggy application code
- Clear audit trail for legal/IP disputes

**Negative:**
- Cannot correct mis-attributed records without a special admin process
- Storage grows monotonically (mitigated by periodic archival to S3)
- Attribution backfill requires inserting new records, not updating old ones

**Correction process (when needed):**
1. Drop the trigger temporarily (requires DBA access — creates audit log entry)
2. Insert corrective records (not modify existing ones)
3. Re-enable the trigger
4. Log the correction in the runbook with justification
