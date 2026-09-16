# Legacy ownerless workspaces — superseded by runtime reproduction

**Status:** the conclusion in every earlier version of this document was wrong.
Codex's runtime reproduction refutes it, and this version states the reproduced
result instead. The earlier reasoning is described only to make the error
traceable, not to defend it.

**Scope:** a migration compatibility defect and its correction. Organization
features remain deferred. No production change is proposed or approved.

## 1. The reproduced finding

An ownerless workspace with an **active admin** becomes **inaccessible** under
the restricted runtime role after `20260909_0007`.

Codex's reproduction (`D:\SourceMind-Operations\migration-0007-ownerless-repro-result.txt`),
PostgreSQL 16.15, synthetic fixture, restricted role `compat_runtime` reporting
`NOSUPERUSER`, `NOBYPASSRLS`, and ownership of zero protected tables:

| Stage | Ownerless workspace, active admin | Owner-backed control |
|---|---|---|
| At `20250817_0005` | workspace/membership/document **1 / 1 / 1** | — |
| After `0005 → 0006 → 0007` | **0 / 0 / 0** | 1 / 1 / 1 |
| Cross-workspace, both | 0 | 0 |
| After the disposable correction | **1 / 1 / 1** | unchanged |

Isolation held throughout — cross-workspace visibility was `0` at every stage,
including after the correction.

## 2. Why I got it wrong

I claimed access required only active membership, citing `sm_workspace_isolation`
from `20260908_0006`. **`20260909_0007` drops that policy** — line 408:

```python
op.execute("DROP POLICY IF EXISTS sm_workspace_isolation ON workspaces")
```

and replaces it with `sm_workspace_select`, which calls
`sm_has_active_workspace_access`:

```sql
SELECT EXISTS (
    SELECT 1
    FROM public.sm_workspace_access_grants AS access_grant
    JOIN public.sm_workspace_bootstraps AS bootstrap          -- INNER JOIN
      ON bootstrap.workspace_id = access_grant.workspace_id
    WHERE access_grant.workspace_id = target_workspace_id
      AND access_grant.user_id = <app.current_user_id>
      AND access_grant.role = ANY(allowed_roles)
      AND access_grant.status = 'active'
      AND access_grant.departed_at IS NULL
      AND bootstrap.deleted_at IS NULL )
```

`sm_workspace_access_grants` is populated from **every** membership.
`sm_workspace_bootstraps` is populated only
`WHERE created_by_user_id IS NOT NULL`. The join between them is an **inner**
join, so a workspace with no creator has no bootstrap row, and a perfectly
valid active grant authorizes nothing.

**I analysed the policy `0006` installs and never checked what `0007` replaced
it with.** I quoted, as evidence, a policy that does not exist at head. That is
the same error I had been correcting in other people's reasoning throughout this
branch — concluding about a system from one layer's state — and a static read
could not have caught it. Only running the migrations did.

## 3. The correction, and what it does not do

Codex's smallest correction keeps the access function and role checks intact and
makes the lifecycle row independent of creator provenance:

1. `sm_workspace_bootstraps.created_by_user_id` becomes nullable.
2. One lifecycle row for **every** workspace, preserving `NULL` where no
   creator is known.
3. `sm_sync_workspace_bootstrap()` upserts unconditionally, including
   creator-null rows.
4. Owner-bootstrap authorization is **unchanged**:
   `bootstrap.created_by_user_id = target_user_id` stays false for `NULL`, so
   **nobody gains an ownership-claim path**.

This restores access **without assigning ownership**, without promoting any
member, and without inventing a creator. The simulation confirmed it: the
legitimate admin regained `1 / 1 / 1` while cross-workspace visibility stayed
`0`.

## 4. The inventory — no production lookup needed

`D:\SourceMind-Operations\railway-rehearsal\restored-0005-inventory.txt`, taken
from the verified restore at `20250817_0005`. Columns are workspace id, members,
owners, documents, memories:

| Workspace | Members | Owners | Docs | Memories |
|---|---|---|---|---|
| `00000000-0000-4000-8000-000000000010` | 1 | 0 | 399 | 1233 |
| `1f83a7aa-3cb2-48c5-b444-a4d6719b7e55` | **0** | 0 | 2 | 5 |
| `451e69b4-ed93-4615-9f32-7f747853fce6` | 2 | 0 | 300 | 1211 |
| `4820b5df-f746-4b7a-93ef-ec07224222d7` | 1 | 0 | 6 | 33 |
| `56507884-4646-4d20-99a8-16a03dc2315e` | 1 | 0 | 3 | 7 |
| `63102ed1-4f6d-4776-8c03-9aaa39ccfafe` | 2 | 0 | 1 | 13 |
| `6b5e6184-1295-439d-bddb-d80979f45a7f` | 1 | 0 | 300 | 1158 |
| `a054135d-89f2-4059-8f3e-ca2eeb892040` | 1 | 0 | 300 | 1107 |

Totals `8 | 9 | 1311 | 4767`. **All eight have zero owners**, confirming the
premise I had listed as unverified. Seven have at least one member; exactly one,
`1f83a7aa-…`, has **none**.

This supersedes the production queries earlier versions of this document asked
for. They are not needed and should not be run.

### The evaluation workspace

`451e69b4-ed93-4615-9f32-7f747853fce6` is the workspace id recorded in
`evaluation/data/sourcemind_id_map.json`, which maps 1156 memory UUIDs to 300
ground-truth artifacts. The inventory shows it holding 300 documents and 1211
memories with 2 members.

Worth flagging rather than glossing: **three workspaces hold 300 documents**
(`451e69b4`, `6b5e6184`, `a054135d`), so document count alone does not identify
the evaluation corpus. The id map is what ties `451e69b4` to the evaluation
specifically. Nothing available maps any id to the *name* `Eval Run 4`, so I
still do not assert that identification — and nothing now depends on it.

## 5. What actually needs deciding

The migration correction handles seven of the eight. It is not a data decision
at all; it is a code fix, and it restores the access those workspaces' existing
members already legitimately had.

That leaves **one** genuine residual: `1f83a7aa-3cb2-48c5-b444-a4d6719b7e55`,
with **zero members** — 2 documents, 5 memories. No migration fix reaches it,
because there is no member whose access could be restored. Its options are
unchanged and small: leave it as it is, or read its 2 documents by direct
database access if anyone ever wants them. Given the volume, this is close to a
non-question, and it needs no production write.

**No owner assignment is required for any of the eight.** The rehearsal's
owner mapping to `cf9ddf79-…` applied to the **restored copy only** and is not
proposed for production.

## 6. Corroboration worth recording

The rehearsal independently found the same `0006` downgrade defect that
`417eb6e` fixed — `workspace_members` left with `FORCE ROW LEVEL SECURITY`
after downgrade, every other table receiving `NO FORCE`. Two separate routes to
the same finding, and the PostgreSQL 18 CI lane now guards it.

The rehearsal also confirms production runs **PostgreSQL 18.6** — the dump was
streamed directly from it. The CI lane runs 18.2 (see the round-trip workflow);
that delta is stated in the release notes and unchanged by this document.

## 7. Deferred, unchanged

Organization-level administration, invitation workflow, and ownership transfer
remain out of scope and unimplemented. §2 sharpens why the third matters: the
absence of any membership-granting route is why a zero-member workspace has no
in-product remedy.
