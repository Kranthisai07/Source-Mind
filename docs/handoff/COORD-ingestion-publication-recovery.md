# Coordination: ingestion publication recovery

This backend change is based on tested candidate
`16167ee9c18859281bac3113fa70d5ef47c4e62c`. It is limited to recovery
after an ingestion task publication exception.

## CI ownership

Codex does not change `.github/workflows/security-acceptance.yml`. Claude
retains workflow ownership and should add the exact nodes below after
cherry-picking the reported backend commit.

## Security-acceptance integration

Add these three nodes to the existing sorted expected-node list:

```text
tests/integration/test_backend_review_real_services.py::test_concurrent_recovery_attempts_publish_once
tests/integration/test_backend_review_real_services.py::test_publication_failure_retry_recovers_pending_document
tests/integration/test_backend_review_real_services.py::test_recovered_job_id_is_pollable_through_real_route
```

The collect-only label, expected count, JUnit total, and passed assertion change
from 19 to 22. The test file is already included in both collection and
execution commands. Keep the existing disposable PostgreSQL/Redis environment
and restricted `sourcemind_test` role.

## Local evidence

- Failing-first run: exactly three selected tests failed in 17.01 seconds.
- Corrected exact three nodes: 3 passed in 16.78 seconds.
- Focused existing and new contract: 18 passed in 23.56 seconds.
- Full security-acceptance collection: exactly 22 node IDs.
- Scoped Ruff and `git diff --check`: passed before documentation.
- No workflow or migration file changed.

A broker publication exception remains ambiguous and recovery is at-least-once;
the implementation deliberately does not claim exactly-once delivery. Landing,
CI execution, merge, and deployment remain separate.
