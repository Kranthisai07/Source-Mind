# Defect: `deferred` silently discards the resolution note

> **RESOLVED 2026-09-14** by Codex in `04babab`, merged into
> `frontend-ci-claude` as `aecf620`, recorded as D-010. The deferred UPDATE now
> writes `resolution_note` and `resolver_id` alongside `status` and
> `revisit_at`, and still leaves `resolved_at`, `blocks_derivation` and both
> memories alone. Verified end to end against a running system, not just by
> unit test — see the `deferred` cases in
> `apps/web/src/pages/conflictResolution.e2e.test.js`. Kept for the record;
> nothing here is outstanding.

**For:** Codex (owns `apps/api/**`)
**From:** Claude (owns `apps/web/**`) — found while exercising conflict
resolution end to end. Not fixed here: the file is outside my ownership.
**Severity:** user input accepted and destroyed, with a 200 response and no
error anywhere. Same class as the `note` vs `resolution_note` bug, which was
invisible for the same reason.

## Where

`apps/api/sourcemind/services/conflict/resolver.py`, in `resolve_conflict`,
the `deferred` branch — the `return True` at the end of that branch (line 386
at commit `90eb134`).

```python
elif resolution_type == "deferred":
    if not revisit_at:
        raise ValueError("revisit_at required for resolution_type='deferred'")
    await session.execute(
        text("UPDATE memory_conflicts "
             "SET status = 'deferred', revisit_at = :rat "
             "WHERE id = CAST(:cid AS uuid) RETURNING id"),
        {"rat": revisit_at, "cid": str(conflict_id)},
    )
    log.info("conflict_deferred", ...)
    return True          # <-- returns BEFORE the shared UPDATE below
```

That early return skips the block every other resolution type reaches:

```python
UPDATE memory_conflicts
SET status = :status,
    resolver_id = CAST(:rid AS uuid),
    resolved_at = NOW(),
    resolution_note = :note,
    blocks_derivation = FALSE
WHERE id = CAST(:cid AS uuid)
```

So for `deferred`, three columns are never written: `resolution_note`,
`resolver_id`, and (correctly) `resolved_at`.

## Why it is user-visible

The resolution-note field in `apps/web/src/pages/ConflictDetail.jsx` is shown
for **every** action, `deferred` included. The client sends it — `ResolveBody`
declares `resolution_note`, FastAPI parses it, and `resolve_conflict` receives
it as a populated argument. It is then dropped on the floor.

The user types an explanation of why they are deferring, sees
"Conflict resolved · Defer · saved", and the explanation does not exist. The
API returns 200. Nothing is logged. This is precisely the shape of the earlier
`note` defect: the write path is missing, not broken, so no error is possible.

`resolver_id` being unset is the same problem in a different column: there is
no record of **who** deferred the conflict, only that it was deferred.

## Reproduction

Against a disposable stack (`docs/handoff/RESOURCE_LEASES.md`), with any open
conflict:

```bash
curl -s -X POST "$API/v1/conflicts/$CONFLICT_ID/resolve" \
  -H 'Content-Type: application/json' \
  -d '{"resolution_type":"deferred",
       "revisit_at":"2026-12-24T15:30:00Z",
       "resolution_note":"Waiting on the platform team."}'
# -> 200
```

Then:

```sql
SELECT status, resolution_note, resolver_id, revisit_at
FROM memory_conflicts WHERE id = '<CONFLICT_ID>';
--  deferred | NULL | NULL | 2026-12-24 15:30:00+00
```

`status` and `revisit_at` are correct. `resolution_note` and `resolver_id`
are NULL despite a note having been sent.

Observed on 2026-09-14 driving the real page through the real API; the
frontend test that records it is
`apps/web/src/pages/conflictResolution.e2e.test.js`, the case named
`KNOWN DEFECT: the note is silently discarded on defer`. It asserts the
**current** behaviour deliberately, so the defect is recorded rather than
hidden. When this is fixed that test will fail, and it should then be inverted
to expect the note.

## Expected contract

A deferral is a decision with an author and a rationale, so:

- `resolution_note` is persisted exactly as sent, including when null.
- `resolver_id` records the user who deferred it. (If `resolver_id` is meant
  to mean "who settled it" and a deferral is not a settlement, then
  `reviewed_by` / `reviewed_at` are the right columns — but one of them must
  be written, because "nobody deferred this" is not a true statement.)
- `resolved_at` stays NULL. A deferral is not a resolution, and the current
  behaviour is right.
- `status` is `deferred` and `revisit_at` is the given instant. Both already
  correct.
- `blocks_derivation` is deliberately **not** cleared, since an unresolved
  conflict should keep blocking. Worth confirming that is intended rather than
  incidental to the early return.

## Note on scope

Only the write is missing. `revisit_at` binding is correct — the datetime is
bound as a `datetime`, not an ISO string, which is what the comment in that
branch is about, and that part is verified working: a `datetime-local` of
`2026-12-24T09:30` entered in a UTC-6 zone stored as `2026-12-24 15:30+00`.
