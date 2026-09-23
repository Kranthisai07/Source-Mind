# Defect: concurrent owner removals can leave a workspace with zero owners

**For:** Codex (owns `apps/api/**`)
**From:** Claude — triaging Codex Review's automated findings. Not fixed here:
`apps/api/alembic/**` is yours, and this is a migration change.
**Source:** Codex Review, P1, raised against `4373563`.
**Classification: CONFIRMED — still applies at current head `0c73ebf`.**

## The claim

> When a workspace has two active owners and concurrent transactions remove or
> demote one owner each, both trigger executions can observe the other owner in
> `sm_workspace_access_grants` and pass this `NOT EXISTS` check under
> PostgreSQL's normal MVCC behavior. Both transactions can then commit, leaving
> the workspace with no active owner.

## The code

`apps/api/alembic/versions/20260909_0007_rls_context_hardening.py`, in
`sm_protect_last_workspace_owner()` (~line 349):

```sql
IF ( NEW.role <> 'owner' OR NEW.status <> 'active'
     OR NEW.departed_at IS NOT NULL )
   AND NOT EXISTS (
       SELECT 1 FROM public.sm_workspace_access_grants AS other_owner
       WHERE other_owner.workspace_id = OLD.workspace_id
         AND other_owner.user_id <> OLD.user_id
         AND other_owner.role = 'owner'
         AND other_owner.status = 'active'
         AND other_owner.departed_at IS NULL )
THEN
    RAISE EXCEPTION 'last active workspace owner cannot be removed'
```

The `DELETE` branch above it has the same shape.

## Why it still applies

This is textbook **write skew**. The guard reads a row set that a concurrent
transaction is simultaneously invalidating, and nothing forces the two to
serialize:

- Under `READ COMMITTED` — the default, and what the application uses — each
  transaction's `NOT EXISTS` sees the snapshot taken at statement start. Owner A's
  removal does not yet see owner B's removal, and vice versa. Both find "another
  owner still exists", both pass, both commit. Zero owners remain.
- Verified against head: **no serialization exists anywhere in the path.** I
  grepped `20260909_0007` and `20260916_0008` for `FOR UPDATE`, `FOR SHARE`,
  `pg_advisory*`, `LOCK TABLE`, `SERIALIZABLE` and `REPEATABLE READ`. The only
  two `FOR UPDATE` occurrences are **RLS policy clauses** —
  `CREATE POLICY sm_workspace_update ON workspaces FOR UPDATE USING (...)` and
  the equivalent on `workspace_members` — which declare which command a policy
  governs. They take no locks.
- `20260916_0008` does not touch this function.
- The existing coverage,
  `test_database_rejects_removing_the_last_active_owner`, opens two sessions
  **sequentially** (`remove_backup`, then `remove_last`). It proves the guard
  works when the transactions do not overlap, which is precisely the case that
  was never in doubt.

I have not executed a concurrent reproduction. The reasoning is from the source
and PostgreSQL's documented `READ COMMITTED` behaviour; if you want it
demonstrated before acting, that is a reasonable thing to ask for and the
disposable-cluster harness already exists.

## Why it matters here specifically

An ownerless workspace is not a hypothetical state in this system — it is the
exact condition `20260916_0008` was just written to survive. That fix restores
*member* access to legacy ownerless workspaces; it does not stop new ones being
created. This race is a live route to manufacturing one, and the eight legacy
workspaces are evidence that the condition persists once reached.

It also has no in-product remedy. There is no route that adds a member or
transfers ownership (`POST /v1/workspaces` is the only membership-creating
route, and it fires at creation), so a workspace that loses its last owner this
way cannot be given another without a direct database write.

## Expected contract

Serialize the invariant before checking it. Options, in the order I would
consider them — but the choice is yours:

1. **Lock the workspace row** in the trigger before the `NOT EXISTS`:
   `PERFORM 1 FROM workspaces WHERE id = OLD.workspace_id FOR UPDATE;`
   Cheap, workspace-scoped, and forces the two transactions into a queue.
2. **Advisory lock** keyed on the workspace id, if taking a row lock on
   `workspaces` for a `workspace_members` write is undesirable.
3. **A deferred constraint trigger** that re-checks at commit — correct, but a
   larger change and it moves the failure to `COMMIT`.

Whatever is chosen, the regression worth having is two genuinely concurrent
transactions each removing a different owner, asserting exactly one succeeds. A
sequential test cannot distinguish the fixed code from the broken code.

## Not release-blocking on its own — your call

It needs two active owners and simultaneous removals. Per the restored-0005
inventory, **all eight current workspaces have zero owners**, so no production
workspace can hit it today. I am recording it as confirmed rather than urgent,
and deferring the severity judgement to you.
