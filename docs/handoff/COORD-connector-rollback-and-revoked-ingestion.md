# Coordination: connector rollback guard and revoked ingestion terminal state

Backend changes are scoped to the two retained findings against `31ae8bb`.

## CI integration

1. Cherry-pick the reported Codex commit onto the PR branch.
2. Keep the existing signed acceptance node in the exact-selection contract:
   `tests/integration/test_security_acceptance_real_services.py::test_signed_api_role_matrix_revocation_and_worker_reauthorization`.
   Its final database assertion now requires `failed`/`failed` plus zero
   memories after the actual API revokes the submitting member.
3. Run `tests/unit/test_migration_regressions.py` and
   `tests/unit/api/test_workspace_revocation.py` in the API unit lane.
4. Extend the PostgreSQL 18 migration lane with a synthetic Slack connector and
   sync log at head. Assert `alembic downgrade 20250817_0005` fails, the
   revision remains `20260916_0009`, and both rows remain. Explicitly remove
   only those synthetic fixtures, then retain the existing compatible
   downgrade/re-upgrade assertions.

The PG18 step must not treat an arbitrary nonzero exit as success: assert the
guard message, unchanged revision, and preserved connector/log rows. Do not
delete or relabel any non-fixture connector to make rollback pass.

## Release contract

Stop the old API after blocking producers and draining workers, before running
the migration. Do not run the old `8072e42` application with the new restricted
credential or a startup-command override; neither combination has been
demonstrated. Restore it only with the recorded pre-release binding,
credentials, configuration, and startup command after a compatible downgrade
or an isolated restore of the fresh pre-migration backup.
