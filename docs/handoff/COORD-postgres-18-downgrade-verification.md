# Coordination: verifying the corrected 0006 downgrade on PostgreSQL 18

**For:** Codex (owns `apps/api/**`, `infra/**`)
**From:** Claude (owns `apps/web/**`, `.github/workflows/**`)
**Subject:** closing the version gap on `417eb6e`
**Status:** IMPLEMENTED. `.github/workflows/migration-roundtrip-pg18.yml` now
runs the cycle described in §4. Migration behaviour was not modified — the lane
only observes. Migration *assertions* are the part I want your eyes on (§7).

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

**The limit, stated correctly.** An earlier draft of this document claimed
nobody had run anything against PostgreSQL 18. That was wrong and overstated
the gap, so it is corrected here rather than quietly edited away.

What already exists and is retained as evidence:

- **The forward upgrade on populated PostgreSQL 18.6 succeeded.** D-007 records
  the deployed Railway service verified route by route on 2026-09-07 — 56
  route-checks across two non-member profiles plus a member-side regression,
  with database state inspected afterwards. That service runs the migrated
  schema on production PostgreSQL 18.6 (`ARCHITECTURE.md`). The upgrade
  direction is therefore evidenced on real populated data at the real version.
- **The production role state was inspected.** The rollout document's
  `rolsuper` / `rolbypassrls` and table-ownership query was run as part of that
  work, and its expectations are recorded in
  `SECURITY_FOUNDATION_ROLLOUT.md`.

Neither is re-run here, and no new production export is requested.

What is genuinely missing is narrower, and all of it sits on the *reverse*
direction:

1. The **downgrade** has never been executed on any PostgreSQL, at any version.
2. The **re-upgrade after a downgrade** likewise.
3. The corrected downgrade (`417eb6e`) **postdates every piece of evidence
   above**, so nothing on record covers the fixed code path at all.
4. The unit regression test for it mocks `op` and executes no SQL. It proves
   the statement is emitted; it cannot prove PostgreSQL accepts it, or that the
   resulting flags match the schema's prior state.

## 3. Reusing existing evidence

Per the owner: reuse existing backup/rehearsal evidence; no new production
export is required. Nothing below asks for one. The existing record in
`SECURITY_FOUNDATION_ROLLOUT.md` — fresh and populated disposable migration
checks reaching `20260909_0007`, the 2026-09-14 secret-injected WSL provision,
and the role/ownership query — remains valid for PostgreSQL 16 and is not
re-run here. What is missing is a version lane, not more data.

## 4. What was built

`.github/workflows/migration-roundtrip-pg18.yml`, plus three helpers under
`.github/scripts/`. No migration, model or `infra/` file was touched: the lane
only observes.

**The image is pinned by digest, not by tag.** The `pg18` tags track the latest
patch release, so a tag alone would silently change the version under test
between runs and make this evidence unreproducible:

```
pgvector/pgvector:0.8.1-pg18@sha256:508c5290cda481d4f5f846446a26e9c1b804766828a394a5861de1b348a18b4c
```

pgvector 0.8.1 — the same pgvector version as the pg16 image pinned in
`infra/docker-compose.security-test.yml`, so the PostgreSQL major is the only
variable between the two lanes. The job records `SELECT version()` and
`SHOW server_version` into the uploaded evidence, and **fails** if the server
is not an 18.x.

The cycle, in order:

1. Provision roles via `apps/web/e2e/init_roles.sql`, then assert neither role
   is a superuser and neither may bypass RLS. Without that, every visibility
   assertion below would be vacuously true.
2. `alembic upgrade 20250817_0005` and **measure** the RLS flags there. The
   baseline is recorded, not written down — a hand-written expectation would
   only assert what somebody believed the baseline was, which is the exact
   class of mistake this whole effort has been correcting.
3. `alembic upgrade head`.
4. Seed real rows (`seed_base.sql`, `seed_conflicts.sql`) and record counts
   plus an `md5` digest of memory content. The job fails if the seed did not
   produce rows: a round trip over an empty database proves nothing about data.
5. `alembic downgrade 20250817_0005`; capture flags and counts.
6. `alembic upgrade head` again; capture flags and counts.
7. Connect as `sourcemind_test` and, using the transaction-local
   `set_config('app.current_user_id', …, true)` the application itself uses,
   record what an active member, a non-member, and a connection with no
   context can each see.

**The gate is the only thing that decides the job.** It runs with `if:
always()`, so a phase that was skipped fails loudly rather than leaving a green
tick on a job that proved nothing — the failure mode the web integration gate
caught for real on an earlier revision, where jest exited 0 having run nothing.
No earlier step's exit code is treated as evidence on its own. It asserts:

- every phase produced its evidence file;
- the post-downgrade flags equal the **measured** baseline, table for table;
- counts and the content digest are identical across seed → downgrade →
  re-upgrade;
- after re-upgrade the member sees exactly what the database holds, and both
  the non-member and the no-context connection see **zero**.

I validated the gate against synthetic evidence before pushing, including a
case reproducing the precise `417eb6e` bug (`workspace_members|false|true`
after downgrade). It is caught, named, and fails the job. Also caught: rows
lost, isolation broken, policy failing open, a skipped phase, and a server that
is not PostgreSQL 18.

### First run: the lane found a bug in itself

Worth recording, because it is the gate behaving exactly as intended. On
`d67af47` every phase executed — the digest-pinned image pulled, PostgreSQL 18
confirmed, baseline measured, upgrade, seed, downgrade, re-upgrade and the
restricted-role queries all succeeded — and **only the gate failed**.

The cause was mine, in the harness, not in a migration. The restricted-role
step took the query result with `tail -1`, but `psql` also prints the
`set_config` value and the `COMMIT` command tag, so the captured "count" was
the string `COMMIT`. The gate refused to accept it, which is right: a value it
cannot interpret is not evidence of isolation.

Reproduced locally on PostgreSQL 16.15 before changing anything, running the
identical cycle with the repo's own scripts. That reproduction also confirmed
the migration behaviour independently of CI:

| Assertion | Result on PostgreSQL 16.15 |
|---|---|
| Flags after downgrade vs measured baseline | **identical, all 13 tables** |
| Row counts and content digest across the trip | **unchanged** |
| Active member sees | 8 of 8 memories |
| Non-member sees | 0 |
| No RLS context sees | 0 |

The fix uses `SET LOCAL` and extracts a bare numeric line rather than trusting
position. The corrected step rejects `COMMIT` and an empty result, and accepts
only a count.

**Residual, stated rather than glossed:** there is no `18.6`-pinned pgvector
tag. This lane proves the behaviour on the digest above and records the exact
minor it ran. If that digest is not 18.6 and you need 18.6 specifically, that
needs a custom image or option B — I will not guess which.

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

## 7. What I want from you — the assertions, not the plumbing

The plumbing is mine and it is done. These are migration questions, and I have
deliberately not answered them alone:

1. **Is `20250817_0005` the right baseline?** I chose it because it is the
   revision immediately before 0006 and the one the rollback procedure names.
   If the intended rollback floor is different, the baseline measurement should
   move with it.
2. **Should policy IDENTITY be asserted, not just the flags?** The lane records
   flags. The rollout document claims the downgrade restores "the exact legacy
   policies", which is a stronger statement than flags alone can test. I did
   not make policy-name comparison fatal, because a difference might be
   intentional and I would rather you decide than have me block CI on a
   judgement that is yours. Say the word and I will add it.
3. **Is losing `workspaces.created_by_user_id` and `workspace_members.status`
   on downgrade acceptable?** Both are dropped by design — the lane asserts row
   counts survive, not those columns. If either should survive a rollback, that
   is a migration change and therefore yours.
4. **Should this lane gate merges?** Right now it runs on pushes to the feature
   branches and on PRs that touch `apps/api/alembic/**`. Making it required is
   a repository setting, which is the owner's.

I have not modified migration behaviour, and will not without a demonstrated
defect.
