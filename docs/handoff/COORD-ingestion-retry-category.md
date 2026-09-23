# Coordination: ingestion retry and category contract

This backend change is based on tested release candidate
`351c04b82db6c8610d044d21ae6539ed8cf3436d` and is limited to the two retained
ingestion findings.

## Behavior

- A recoverable worker exception persists `processing` plus
  `pipeline_data.current_stage=retrying`; polling returns `retrying` and no
  terminal error while Celery backoff is pending.
- Exhausted retries and revoked submission access remain terminal `failed`
  outcomes. Revoked work performs no extraction or memory write.
- Optional category remains a free-form value capped at 100 characters. It now
  flows through `MemoryCreate`, the API route, document pipeline data, worker
  execution, and stored memories without defining a new taxonomy.

## CI integration

Cherry-pick the reported Codex commit onto Claude's PR branch. The commit adds
these exact nodes to `.github/workflows/security-acceptance.yml` and changes the
execution/JUnit assertion from ten to fourteen tests:

```text
tests/integration/test_ingestion_worker_contract.py::test_exhausted_retries_are_terminal_failed
tests/integration/test_ingestion_worker_contract.py::test_revoked_access_stays_terminal_without_worker_processing
tests/integration/test_ingestion_worker_contract.py::test_selected_category_reaches_memory_through_worker
tests/integration/test_ingestion_worker_contract.py::test_transient_error_is_retrying_then_worker_succeeds
```

Keep the existing security-acceptance workflow's disposable PostgreSQL service,
restricted `sourcemind_test` role, and real Redis setup. The test has strict
guards for custom disposable targets and otherwise consumes the acceptance
fixtures already provided by CI. External extraction, model, embedding,
relation, and attribution work is stubbed; database authorization, API
persistence, polling, revocation, and worker orchestration remain real. The
separate PostgreSQL 18 migration lane does not need to rerun these worker tests.

## Frontend polling coordination

The current adapter maps only backend `completed` to UI `done`, and the panel
stops only on `done` or `failed`. Therefore backend `retrying` already continues
polling and is not rendered as terminal failure. Claude should add one focused
case to `apps/web/src/pages/ingestPolling.test.js` that returns `retrying` before
`completed`, proves polling continues, and then proves it stops on normalized
`done`. No frontend taxonomy or terminal-state change is requested.
