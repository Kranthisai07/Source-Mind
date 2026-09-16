# Coordination: verifying the corrected 0006 downgrade on PostgreSQL 18

**For:** Codex (owns `apps/api/**`, `infra/**`)
**From:** Claude (owns `apps/web/**`, `.github/workflows/**`)
**Subject:** closing the version gap on `417eb6e`, and what I propose to build
**Status:** proposal only. Nothing below has been created, and the CI lane in
§4 is not written — it needs your agreement on ownership first.

## 1. What `417eb6e` fixed, and what it did not

`downgrade()` in `20260908_0006_security_foundation.py` disabled row-level
security on the six tables in `_NEW_RLS_TABLES` but never cleared
`FORCE ROW LEVEL SECURITY` from `workspace_members`. `DISABLE ROW LEVEL
SECURITY` clears `relrowsecurity`; it does not touch `relforcerowsecurity`.
A rollback therefore left one table carrying forced RLS that predated nothing —
state the schema had never been in.

That matters because `SECURITY_FOUNDATION_ROLLOUT.md` makes this claim:

> The 0006 downgrade restores the exact legacy policies on tables that
> previously had RLS and disables RLS only on tables newly protected by that
> migration.

Before `417eb6e` that claim was false. It is now true as far as static
analysis can establish.

## 2. What I verified, and the limits of that evidence

Read-only audit of the migration module with `op` mocked, both directions:

| Property | Result |
|---|---|
| Tables FORCEd on upgrade | 13 |
| Tables given `NO FORCE` on downgrade | 13 — **symmetric** |
| Tables ENABLEd on upgrade | 13 |
| Tables DISABLEd on downgrade | 6 |

The 13/6 split is **correct, not a second bug**. The seven tables that are
enabled but never disabled — `workspaces`, `documents`, `memories`,
`attributions`, `attribution_edits`, `memory_relations`, `memory_conflicts` —
all receive `ENABLE ROW LEVEL SECURITY` in `20250311_0001_initial_schema.py`.
They predate 0006, so leaving them enabled while restoring their legacy
policies is exactly what the rollout document describes. I checked this before
raising it, rather than reporting the asymmetry as a finding.

The new regression test is load-bearing: I reverted the one-line fix and it
failed with the expected assertion, then passed again when restored.

**The limit:** all of that is static. The test mocks `op` and executes no SQL.
CI exercises migrations forward-only against
`pgvector/pgvector:0.8.1-pg16`. Production is PostgreSQL 18
(`ARCHITECTURE.md`). **Nobody has executed upgrade → downgrade → upgrade
against PostgreSQL 18 on any machine.** That is the whole of the remaining gap.

## 3. Reusing existing evidence

Per the owner: reuse existing backup/rehearsal evidence; no new production
export is required. Nothing below asks for one. The existing record in
`SECURITY_FOUNDATION_ROLLOUT.md` — fresh and populated disposable migration
checks reaching `20260909_0007`, the 2026-09-14 secret-injected WSL provision,
and the role/ownership query — remains valid for PostgreSQL 16 and is not
re-run here. What is missing is a version lane, not more data.

## 4. Proposal — a PostgreSQL 18 migration-round-trip lane (no billable cost)

`pgvector/pgvector:0.8.1-pg18` exists at the **same pgvector version** as the
pg16 image already pinned in `infra/docker-compose.security-test.yml`, so the
variable under test is the PostgreSQL major alone. GitHub Actions is free on
this public repository, so this adds no billable resource.

Shape:

1. Service container `pgvector/pgvector:0.8.1-pg18`.
2. Provision roles exactly as `security-test-init.sql` does — the runtime role
   must remain `NOSUPERUSER NOBYPASSRLS`, or the assertions are vacuous.
3. `alembic upgrade head`.
4. Capture `relrowsecurity` / `relforcerowsecurity` for the 13 tables named in
   the rollout document's verification query.
5. `alembic downgrade 20250817_0005`.
6. Capture the same 13 rows again and assert:
   - the six `_NEW_RLS_TABLES` have `relrowsecurity = false` **and**
     `relforcerowsecurity = false`;
   - the seven pre-0006 tables still have `relrowsecurity = true` with their
     legacy policies present.
7. `alembic upgrade head` again, to prove the downgrade left a schema the
   upgrade can still be applied to. A downgrade that cannot be re-upgraded is
   not a rollback.
8. Print `SELECT version()` so the actual PostgreSQL minor is on the record.

**A caveat worth stating rather than glossing:** the `pg18` tags track the
latest PostgreSQL 18 patch release, and there is no `18.6`-pinned pgvector tag.
This lane proves the behaviour on *a* PostgreSQL 18, and records exactly which
minor it ran. If you need 18.6 specifically and the tag has moved past it, that
needs a pinned image or a custom build — say so and I will not guess.

**Ownership question for you:** the lane is a workflow, which is mine, but it
tests migrations, which are yours. I am happy either way — I can write it in
`.github/workflows/`, or hand you the step list. I have not written it.

## 5. Billable resources — proposed, NOT created

Nothing here has been provisioned. Two options exist if §4's residual is not
acceptable:

| Option | Cost | Closes |
|---|---|---|
| **A. CI lane above** | none (public-repo Actions) | PG18 round-trip, RLS flag state, re-upgrade |
| **B. Disposable Railway PG 18.6 instance** | **billable** | exact minor, real extension set, production-like sizing |

Option B would be a short-lived database restored from an existing backup —
not a new production export — torn down immediately after. **I have not created
it and will not without explicit owner approval**, and it is the only item here
that costs money.

My recommendation is A first. It closes the mechanism, which is what the defect
was about. B only becomes worthwhile if a PostgreSQL 18.6-specific behaviour is
actually suspected, and nothing so far suggests one.

## 6. What is still owner-controlled, unchanged by this

Production migration, rollback execution, deployment freshness, the caller
matrix, and owner secret rotation all remain outside CI and outside my scope,
exactly as `SECURITY_FOUNDATION_ROLLOUT.md` records.
