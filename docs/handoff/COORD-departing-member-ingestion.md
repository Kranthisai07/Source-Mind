# Coordination: departing-member ingestion terminalization

This backend change is based on
`d3b27fd546bcc1e0aaa09da13b56dba3ed06cb91`. Devin's referenced
`49a00cea8fbf965035569ef75001ee1974352822` is frontend-only; the reproduced
defect was in the current backend handoff transition.

## CI ownership

Codex does not change `.github/workflows/security-acceptance.yml`. Claude
retains workflow ownership and should add the exact nodes below when integrating
this scoped backend commit.

## Security-acceptance integration

After the worker-deduplication handoff's 22-to-25 update, add these two sorted
nodes and change the collect-only label, expected count, JUnit total, and passed
assertion from 25 to 27:

```text
tests/integration/test_ingestion_worker_contract.py::test_failed_handoff_rolls_back_ingestion_terminalization
tests/integration/test_ingestion_worker_contract.py::test_handoff_departure_terminalizes_submitter_ingestion_jobs
```

If the workflow still has its original 22-node list, integrate both coordination
handoffs together and update directly from 22 to 27. The test file is already
included in collection and execution commands. Keep the disposable
PostgreSQL/Redis environment and restricted `sourcemind_test` role.

## Transaction contract

Handoff initiation now locks and terminalizes only the departing submitter's
queued or retrying documents before changing membership from active to
departing. Explicit member revocation uses the same tenant-scoped helper and
retains its existing error contract. Both paths run under ordinary RLS and the
caller's existing authorization; no security-definer function, elevated role,
or post-departure write capability is introduced.

Document terminalization, handoff creation, and membership transition remain in
one transaction. A handoff failure therefore restores document, handoff, and
membership state together. Last-owner checks still run before any document
change. Document row locks also serialize this transition with the worker's
existing execution claim, preserving duplicate-delivery protection.

## Local evidence

- Failing-first exact nodes: 2 failed in 14.94 seconds; queued and retrying
  documents remained nonterminal.
- Executable reproduction: the queued worker delivery was rejected, invoked no
  model stage, wrote zero memories/attribution rows, and left the job pending.
- Corrected exact nodes: 2 passed in 14.82 seconds.
- Full worker contract: exactly 9 collected, 9 passed in 17.84 seconds.
- Existing owner-departure authorization unit check: 1 passed in 3.15 seconds.
- Security-acceptance collection: exactly 27 nodes, including the two new nodes.
- Fresh Alembic upgrade to existing head `20260916_0009` succeeded; no schema or
  migration change is required.

Landing, CI execution, merge, and deployment remain separate.
