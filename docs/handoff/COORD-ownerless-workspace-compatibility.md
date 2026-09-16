# Coordinate ownerless-workspace compatibility into PR #5

## Backend change

Migration `20260916_0008` provides an explicit forward path for databases
already at the previous `20260909_0007` definition. It makes creator identity
nullable in the internal workspace lifecycle table, inserts a lifecycle row for
every workspace, and keeps that state synchronized even when creator provenance
is unknown. It does not assign owners or change application memberships.

The restricted-role database regression is:

```text
apps/api/tests/integration/test_ownerless_workspace_migration_real_db.py::test_ownerless_workspace_membership_survives_migration_round_trip
```

Its 0005 fixture is:

```text
apps/api/tests/fixtures/ownerless_workspace_0005.sql
```

## PostgreSQL 18 lane integration

In `.github/workflows/migration-roundtrip-pg18.yml`:

1. Include the fixture and regression paths in the pull-request path filter.
2. After migrating to `20250817_0005`, load the fixture as the migration owner.
3. Upgrade only to `20260909_0007` and run the exact regression node through
   the restricted runtime URL; require a non-zero result demonstrating the old
   `0/0/0` compatibility failure.
4. Upgrade to head and require the node to pass.
5. Continue the existing downgrade to `20250817_0005` and re-upgrade to head,
   then require the same node to pass again.
6. Preserve the existing PostgreSQL-version, role, RLS-flag, row-count, digest,
   and restricted-role assertions. Add the ownerless fixture rows to the
   expected evidence instead of weakening those checks.

The test itself covers active admin/member access, owner-backed access,
cross-workspace denial, departed-member denial, deleted-workspace denial, and
rejection of an owner claim when `created_by_user_id` is unknown.

## Release handoff correction

Blanket owner assignment is not the compatibility repair and is not approved.
Migration `0008` preserves legitimate membership access without inventing
creator provenance. Organization-level administration, invitation workflows,
and ownership transfer remain deferred future work.

Do not edit or amend the backend commit when integrating it. Cherry-pick the
reported commit into Claude's PR #5 branch, add the PostgreSQL 18 workflow
changes there, and report the resulting integrated revision and CI run.
