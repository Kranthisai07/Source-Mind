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

## 2. What I cannot reconcile, and why

The instruction was to use workspace UUIDs and reconcile the two Run 4 entries
against the recorded inventory. **There is no recorded workspace inventory in
this repository, and no production workspace UUID anywhere in it.**

- The only workspace UUIDs in the docs are synthetic fixtures in
  `SECURITY_FOUNDATION_ROLLOUT.md` (`11111111-…`, `22222222-…`). Promoting
  those would be exactly the fabrication I was told not to commit.
- D-007 records a **name and counts** — `name=Eval Run 4`,
  `total_memories=1167`, `contributors=2`, and "8 live workspaces" — but no
  identifier for any of the eight.
- "Run 4" denotes two different kinds of object in the record: the
  **evaluation run** analysed in D-005 (`DECISIONS.md:184,503,522,563`), and a
  **workspace named `Eval Run 4`** observed during the D-007 verification
  (`DECISIONS.md:859,863`). They share a label; one is a benchmark execution,
  the other a tenant row. Nothing in the repository links either to a UUID.

I will not invent ids, and I will not ask for an eight-owner mapping — §1.3
shows it is not technically required, so requesting one would be asking for
work I cannot justify.

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
2. **The inventory.** If a production workspace inventory with UUIDs exists
   outside this repository, it belongs in the reconciliation. If it does not,
   then the eight are identified only by the aggregate count in D-007, and that
   should be stated plainly rather than implied.

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
