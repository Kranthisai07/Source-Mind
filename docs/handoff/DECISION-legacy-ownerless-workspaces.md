# The eight legacy ownerless workspaces — corrected

**Status:** an earlier version of this document was wrong on two substantive
points and used one misleading word. Those are corrected below rather than
quietly edited out, because the wrong version argued for a production write
that the code does not justify.

**Scope:** a one-time data question. Not a tenancy redesign, not an
authorization change, not a SaaS feature. Organization-level features remain
deferred and no production ownership change is approved or proposed for
execution.

## 1. Corrections

### 1.1 "Reachable by nobody" — WRONG

I claimed an ownerless workspace becomes inaccessible to everyone. It does not.
Access is gated on **active membership of any role**, never on ownership.

`sm_workspace_isolation`, created in `20260908_0006`:

```sql
CREATE POLICY sm_workspace_isolation ON workspaces
USING (
    deleted_at IS NULL
    AND ( id = <app.current_workspace_id>
          OR EXISTS (SELECT 1 FROM workspace_members AS own_membership
                     WHERE own_membership.workspace_id = workspaces.id
                       AND own_membership.user_id = <app.current_user_id>
                       AND own_membership.status = 'active'
                       AND own_membership.departed_at IS NULL) ) )
```

The same shape governs the data tables via `_active_access(...)`: it tests
`status = 'active'` and `departed_at IS NULL`. **Neither predicate mentions
`role`.**

The API layer agrees. Every workspace-scoped route calls
`require_workspace_member` — `analytics.py:43,61,84,108`, `conflicts.py:57`,
`workspaces.py:212,252` — which asserts membership, not rank.

**So: an existing active admin or member of an ownerless workspace retains
full access to it after 0007, including its memories and analytics.** Role
matters only for operations gated on `require_workspace_permission`, such as
`DELETE /v1/workspaces/{id}/members/{user_id}` requiring `ADMINISTER` — and an
active admin satisfies that without an owner existing.

### 1.2 "Setting `created_by_user_id` lets them claim ownership through the product" — WRONG

I claimed the one-row repair "re-arms the existing bootstrap path". It does
not, because there is no route on the other end of that path.

`sm_can_bootstrap_workspace_owner` is an **RLS policy predicate**. It permits
an `INSERT` into `workspace_members`. It does not perform one.

The only route in the codebase that inserts a membership row is
`POST /v1/workspaces` (`workspaces.py:97`), which adds the *creator* as owner
at creation time:

```python
membership = WorkspaceMember(
    workspace_id=workspace.id, user_id=current_user.user_id,
    role=WorkspaceRole.OWNER,
)
```

There is **no route that adds a member to an existing workspace**, and
therefore none that claims ownership of one. `DELETE .../members/{user_id}`
revokes; nothing grants.

**So: setting `created_by_user_id` would grant a database permission that no
API caller can exercise.** As a repair it accomplishes nothing on its own. The
only ways to create that membership today are a direct database write or a new
route — and a new route is deferred SaaS work.

### 1.3 Is an owner technically required? — NO. It is a proposed operational rule.

Nothing in the schema, the policies, or the API requires a workspace to have an
owner. There is no constraint enforcing it, and `20260909_0007`'s backfill
tolerates its absence by leaving `created_by_user_id` NULL.

A workspace with an active admin and no owner is fully functional for read,
write and member administration. Wanting a named accountable owner per
workspace is a **governance preference**, and I presented it as a technical
necessity. It was not one.

### 1.4 "Retire to archival" — WRONG WORD, dropped

Leaving a workspace untouched archives nothing. `deleted_at` stays NULL, the
row stays live, it continues to appear to its active members and its data
continues to be served. "Archival" implied a state transition that no code
performs. The accurate phrase is **"leave unchanged"**, and I have stopped
calling it anything else.

## 2. The recorded identifier, and the Run 4 reconciliation

**Correction to my own correction.** I first wrote that no production workspace
UUID exists anywhere in this repository. That was wrong — I had searched only
`*.md`. A wider search found one:

`evaluation/data/sourcemind_id_map.json`

```
workspace_id               451e69b4-ed93-4615-9f32-7f747853fce6
memory_to_ground_truth     1156 memory UUIDs
ground_truth_artifact_type 300 entries
```

This is a **real production workspace UUID** — the evaluation workspace — and it
is the only one recorded. The remaining seven of the eight have no identifier
anywhere in the repository.

### Reconciling the two Run 4 entries

They are two different kinds of object that share a label, and they do **not**
agree numerically:

| Entry | Source | Figure |
|---|---|---|
| Evaluation **run 4** | `evaluation/data/report_run4.md`, generated 2026-09-03 | 300 ground-truth items; recall@5 0.703 |
| Workspace **`Eval Run 4`** | `DECISIONS.md:859,863` (D-007, 2026-09-07) | 1167 current memories, 2 contributors |
| Memory→ground-truth map | `sourcemind_id_map.json` | **1156** memory UUIDs, workspace `451e69b4-…` |

So one is a benchmark execution and its report; the other is the tenant row the
benchmark's data lives in. The map ties the workspace UUID to 1156 memories,
while D-007 counted 1167 current memories in the workspace named `Eval Run 4`
four days later — **a difference of 11**.

`451e69b4-ed93-4615-9f32-7f747853fce6` is therefore the **strong candidate** for
the `Eval Run 4` workspace, on three grounds: it is the evaluation workspace, it
holds the evaluation corpus, and 1156 is within 1% of 1167. It is **not
confirmed**, because the counts differ and nothing in the repository maps a
workspace UUID to a workspace *name*. Confirming it requires one read-only
lookup of that id against production, which I have not run.

I am not asserting the identification, and I have not invented an id for any of
the other seven, because none is recorded.

## 3. The decision, correctly narrowed

Given §1.1, membership is what determines access, so the question is not about
ownership at all:

> **Does any of the eight workspaces have zero active members?**

- **Workspaces with at least one active member:** nothing is wrong with them.
  No repair, no decision, no production write. They work.
- **Workspaces with zero active members:** unreachable through the API, and —
  per §1.2 — with no in-product remedy, because no route can add a member.
  For these, and only these, there is a real choice:
  1. leave unchanged, and read the data by direct database access if it is
     ever wanted; or
  2. a one-time direct database write creating a membership.

The read-only query that settles it, which I have not run because I have no
production access:

```sql
SELECT w.id, w.name,
       count(m.id) FILTER (WHERE m.status = 'active'
                             AND m.departed_at IS NULL) AS active_members,
       count(m.id) FILTER (WHERE m.role = 'owner' AND m.status = 'active'
                             AND m.departed_at IS NULL) AS active_owners
FROM workspaces AS w
LEFT JOIN workspace_members AS m ON m.workspace_id = w.id
WHERE w.deleted_at IS NULL
GROUP BY w.id, w.name
ORDER BY w.name;
```

`active_members` is the column that matters. `active_owners` is informational.

For the one workspace that does have a recorded id, the narrower lookup is:

```sql
SELECT w.id, w.name, w.deleted_at,
       count(m.id) FILTER (WHERE m.status = 'active'
                             AND m.departed_at IS NULL) AS active_members
FROM workspaces AS w
LEFT JOIN workspace_members AS m ON m.workspace_id = w.id
WHERE w.id = '451e69b4-ed93-4615-9f32-7f747853fce6'
GROUP BY w.id, w.name, w.deleted_at;
```

That single query also settles whether this id is the workspace D-007 saw as `Eval Run 4`, by returning its name.

**On the evaluation data specifically:** D-007 recorded that a member still
received `Eval Run 4`'s full contents through the deployed API *after* the
isolation fix. If that membership is still active, that workspace already has a
reader and needs nothing. No data is at risk in any case — these rows are
untouched by migration, and direct database access reads them regardless.

## 4. For Codex — one reconciled technical conclusion

The questions I could answer from code are answered above. Two remain, and both
are yours:

1. **Confirm or refute §1.1 and §1.2** against your reading. My conclusion is
   that active membership alone grants access, and that no route can create a
   membership on an existing workspace. If either is wrong, the narrowing in §3
   collapses and should be redone.
2. **The inventory.** One production workspace UUID is recorded —
   `451e69b4-ed93-4615-9f32-7f747853fce6`, in
   `evaluation/data/sourcemind_id_map.json`. The other seven are not recorded
   anywhere in this repository; they exist in the record only as D-007's
   aggregate "8 live workspaces". If an inventory exists outside the repo, it
   belongs in the reconciliation. Confirming that the recorded id is the
   workspace named `Eval Run 4` needs one read-only lookup.

## 5. Deferred — recorded, not implemented

Unchanged and still out of scope: **organization-level administration** (an
`organizations` table exists; every authorization decision is workspace-scoped),
**invitation workflow** (no route creates a pending membership), and
**ownership transfer** (nothing reassigns ownership).

§1.2 sharpens why these matter: the absence of any membership-granting route is
not only a gap in future features, it is the reason an
active-member-less workspace has no in-product remedy today.

## 6. Unchanged by this document

Workspace-scoped authorization, isolation, and the CI evidence. This is
documentation only — no code changed, so nothing is re-run and the seven green
workflows on `c2937b3` stand. PR #5 remains unmerged; production changes and
deployment remain pending approval.
