# The eight legacy ownerless workspaces: one decision, and what is deferred

**Status:** decision required from the owner. Nothing here is implemented, and
no production change is proposed for execution before approval.
**Scope:** a one-time data repair. Explicitly *not* a tenancy redesign, not an
authorization change, and not a SaaS feature.

## 1. Why this needs a decision at all

`20260909_0007` backfills `workspaces.created_by_user_id`, but only from a
member who is already an active **owner**:

```sql
UPDATE workspaces
SET created_by_user_id = (
    SELECT member.user_id FROM workspace_members AS member
    WHERE member.workspace_id = workspaces.id
      AND member.role = 'owner' AND member.status = 'active'
      AND member.departed_at IS NULL
    ORDER BY member.created_at, member.id LIMIT 1)
WHERE created_by_user_id IS NULL
```

A workspace with no active owner therefore keeps `created_by_user_id = NULL`.

The product's own recovery path cannot help it. Bootstrapping an owner is
gated on `sm_is_workspace_creator_without_owner`, which begins:

```sql
SELECT target_creator_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
```

With `created_by_user_id` NULL, `target_creator_id` is NULL, so that comparison
is NULL — never true, for anybody. **After migration, an ownerless workspace has
no in-product path back to having an owner.**

So the migration converts these eight from "reachable by anyone" — which was
the D-007 leak, and was wrong — into "reachable by nobody". That is a strict
security improvement and the correct default. It is only a problem for the one
workspace whose contents someone still wants to read through the API.

## 2. What is NOT at risk

**No data is lost or altered by this.** The rows stay exactly where they are.
`Eval Run 4` keeps its 1167 current memories; D-007 recorded that state after
the deployed verification runs, and nothing since touches row data.

What changes is **API reachability**, not existence. Anything with direct
database access — the migration owner role, a backup, an export — reads these
workspaces normally. This distinction is the whole reason the decision below is
small: preserving the evaluation data does not require granting anyone access.

## 3. The decision

**For each of the eight workspaces: does it get a named accountable owner, or
is it retired to archival?**

That is the entire decision. It does not need to be answered the same way for
all eight, and answering it per workspace is what keeps the blast radius small.

Recommended split, for the owner to confirm or overrule:

| Workspace | Recommendation | Reasoning |
|---|---|---|
| `Eval Run 4` (1167 memories) | **Name one owner** | Evaluation data with ongoing analytical value. API reachability is worth restoring for exactly one principal. |
| The other seven | **Retire to archival** | No demonstrated need for API access. Leaving them ownerless is the safest state and needs no production write at all. |

**Explicitly rejected:** assigning the owner's account to all eight. The owner
ruled it out, and it is also the wrong shape — it would manufacture eight
memberships to solve one access need, and each is a standing grant that nobody
would later remember to revoke.

If `Eval Run 4` gets an owner, the minimal repair is a **single row**:

```sql
-- One workspace, one principal, recorded deliberately.
UPDATE workspaces SET created_by_user_id = :principal WHERE id = :eval_run_4;
```

That alone re-arms the existing bootstrap path, letting that principal claim
ownership through the product rather than having a membership fabricated for
them in SQL. It writes one column on one row, invents no mechanism, and leaves
the authorization model untouched.

An alternative, if the owner prefers no production write whatsoever: **export
the evaluation data** using direct database access and leave all eight
ownerless. That satisfies "preserve the evaluation data" with zero production
mutation. It is the smaller action, and it is a legitimate answer to the
decision rather than a dodge.

## 4. Preconditions before any repair runs

1. **Confirm the premise.** This document assumes all eight lack an active
   owner. That has not been verified against production by me and must be,
   read-only, before anything is written:

   ```sql
   SELECT w.id, w.name, w.created_by_user_id IS NULL AS no_creator,
          count(m.id) FILTER (
            WHERE m.role = 'owner' AND m.status = 'active'
              AND m.departed_at IS NULL) AS active_owners
   FROM workspaces AS w
   LEFT JOIN workspace_members AS m ON m.workspace_id = w.id
   WHERE w.deleted_at IS NULL
   GROUP BY w.id, w.name, w.created_by_user_id
   ORDER BY w.name;
   ```

   If any of the eight already has an active owner, `0007` backfills it and it
   is not part of this decision at all.

2. **Order matters.** The repair is a post-migration step. Running it before
   `0007` would have the backfill overwrite nothing — `created_by_user_id`
   would already be set — but the ordering should be deliberate rather than
   incidental. Apply migrations, verify, then repair.

3. **Reuse the existing backup evidence.** No new export is needed; the
   rollout's existing backup/rehearsal record covers this.

## 5. Deferred — recorded, not implemented

The following are **out of scope for this release** and no code for them exists
or should be written now. They are recorded here so the gap is documented
rather than rediscovered.

### Organization-level administration
There is an `organizations` table and `workspaces.organization_id`, but no
organization-level role, permission or administration surface. Every
authorization decision in the system is workspace-scoped. An org admin who
could see or manage workspaces they are not a member of does not exist, and
deliberately so — it would be a new tenancy tier, which this release is not
introducing.

### Invitation workflow
No route creates a pending membership, and `workspace_members.status` has no
`invited` state in use. Members exist or they do not. Adding somebody to a
workspace today means writing a row.

### Ownership transfer
No route reassigns ownership. `sm_can_bootstrap_workspace_owner` covers a
creator claiming an *unowned* workspace; there is nothing for handing an owned
workspace to somebody else, and nothing preventing the last owner from
departing and recreating precisely the ownerless condition this document is
about.

**The connection worth naming:** a real invitation and ownership-transfer
workflow is what stops this situation recurring. This repair fixes eight rows
of history; it does not stop a ninth appearing. That is a reason to schedule
the deferred work, not a reason to widen this release.

## 6. What this release keeps unchanged

- The workspace-scoped authorization model, exactly as it is.
- Isolation. Nothing here weakens a policy, and the recommended repair grants
  access to one principal on one workspace rather than broadening any rule.
- CI and rehearsal evidence. No code changes here, so nothing is re-run: the
  seven green workflows on `c2937b3` stand, including the PostgreSQL 18
  round-trip.
