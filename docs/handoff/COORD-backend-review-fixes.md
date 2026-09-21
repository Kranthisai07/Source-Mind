# Coordination: backend review fixes

This backend change is based on tested candidate
`1e5d7bba87d221f7fa6182923d9caa6cf2d7fdc7` and is limited to concurrent
ingestion idempotency, GitHub connector checkpoint correctness, and the 429
`Retry-After` response contract.

## CI ownership

No workflow file is changed in the Codex commit. Claude retains ownership of
`.github/workflows/security-acceptance.yml` and should integrate the exact nodes
below after cherry-picking the reported commit.

## Security-acceptance integration

Add this file to both the collect-only and execution commands:

```text
tests/integration/test_backend_review_real_services.py
```

Add these exact five nodes to the sorted expected-node list:

```text
tests/integration/test_backend_review_real_services.py::test_failed_request_releases_reservation_for_retry
tests/integration/test_backend_review_real_services.py::test_identical_concurrent_submissions_share_one_result
tests/integration/test_backend_review_real_services.py::test_in_progress_wait_is_bounded_and_expired_lease_recovers
tests/integration/test_backend_review_real_services.py::test_real_redis_429_exposes_positive_retry_after
tests/integration/test_backend_review_real_services.py::test_same_key_with_different_payload_conflicts_atomically
```

The existing expected count and JUnit assertion therefore change from 14 to
19. Keep the existing migrated disposable PostgreSQL service, restricted
`sourcemind_test` role, and real Redis service. In CI the new fixtures consume
the existing `TEST_DATABASE_URL`, `TEST_REDIS_URL`, and
`SECURITY_TEST_ALLOW_DISPOSABLE=1` contract; the custom `BACKEND_REVIEW_*`
variables are only guarded overrides for collision-free local runs.

## Unit integration

The ordinary backend unit lane must collect the new connector node:

```text
tests/unit/connectors/test_failure_checkpoint.py::test_failed_artifact_preserves_checkpoint_and_is_retried
```

The existing receiver and security-foundation unit files were updated for the
new Redis Lua return shape. A focused command is:

```bash
python -m pytest -o addopts='' -q -p no:cacheprovider \
  tests/unit/ingestion/test_receiver.py \
  tests/unit/connectors/test_connector.py \
  tests/unit/connectors/test_failure_checkpoint.py \
  tests/unit/api/test_security_foundation.py
```

No migration or rollback workflow needs modification or repetition. Landing
and release remain separate from this backend commit.
