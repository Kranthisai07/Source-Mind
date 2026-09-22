# Coordination: ingestion worker duplicate delivery

This backend change is based on tested candidate
`0d762ad4afc6a7d2f42d41f7d5d4295010178fdb`. It is limited to making the
worker transaction safe when ambiguous publication recovery delivers the same
document more than once.

## CI ownership

Codex does not change `.github/workflows/security-acceptance.yml`. Claude
retains workflow ownership and should add the exact nodes below when integrating
this scoped backend patch.

## Security-acceptance integration

Add these three nodes to the existing sorted expected-node list:

```text
tests/integration/test_ingestion_worker_contract.py::test_ambiguous_publication_redelivery_preserves_committed_output
tests/integration/test_ingestion_worker_contract.py::test_concurrent_duplicate_deliveries_commit_output_once
tests/integration/test_ingestion_worker_contract.py::test_stale_processing_state_is_recovered_after_worker_crash
```

The collect-only label, expected count, JUnit total, and passed assertion change
from 22 to 25. The test file is already included in both collection and
execution commands. Keep the disposable PostgreSQL/Redis environment and the
restricted `sourcemind_test` role.

## Execution contract

The worker acquires a non-waiting PostgreSQL row lock on the document before
pipeline execution and holds it through the output transaction. A concurrent
delivery observes `in_progress` without running model stages. Memory,
attribution-edit, attribution, relation, and completed-document writes commit
together; a delivery after that commit returns the stored result unchanged.

Pipeline failures roll back to a savepoint while retaining the row claim, then
persist `retrying` or terminal `failed` state before releasing the claim.
Celery's scheduled retry therefore remains executable. A process crash releases
the PostgreSQL lock and rolls back uncommitted output, while a crash after commit
is handled by the completed-state check on redelivery. This is at-least-once
task delivery with idempotent committed output, not exactly-once delivery.

## Local evidence

- Failing-first exact nodes: two expected failures demonstrated duplicate
  memory and attribution output; the stale-processing recovery guard passed.
- Corrected exact nodes: 3 passed in 5.63 seconds.
- Full worker contract: exactly 7 collected, 7 passed in 6.54 seconds.
- PostgreSQL identity: disposable PostgreSQL 16.15, restricted
  `sourcemind_test` (`NOSUPERUSER`, `NOBYPASSRLS`), with real Redis database 15.
- Scoped Ruff and formatting checks passed.
- No schema, migration, workflow, production, or deployment change is required.

Landing, CI execution, merge, and deployment remain separate.
