# Automatic source ingestion: normative contract

Status: the contract the implementation and its tests are written against. It describes
behavior this repository is being changed to have, not behavior that is deployed. Evidence
levels are stated per feature in the pull requests that implement them.

Scope of the first release: GitHub pull request bodies, issue bodies, PR reviews, review
comments, issue/PR comments and default-branch commit messages, synchronized on a schedule
by a direct GitHub API connector. Out of scope: repository files and docs, discussions,
other sources, webhooks, generated answers.

Vocabulary. **Scope** = (connector, repo, artifact_type). **Handoff** = the receiver's committed
transaction (Document `pending` + ArtifactLink + dispatch outbox). **Publication** = the
ingestion worker's final transaction that makes memories live. Delivery is at-least-once;
every effect is idempotent or fenced. Nothing here claims exactly-once processing.

## C1 Authorship is per authored segment

Each segment is its own artifact attributed only to its own author: `commit` (`o/r/commits/{sha}`,
author = commit author), `pull_request` body, `pull_review` (non-empty body), `pull_review_comment`,
`issue` body, `issue_comment` (also PR conversation comments). A segment's document carries contextual
labels (repo, number, title, url) separately from the authored text; the labels are never part of the
attributed content. A commit with co-author trailers is `shared`: uncredited, shown as multiple authors,
no percentages. Unknown authors stay unresolved. The connecting user is never credited. Noise filters are
explicit and counted per reason in the sync log (`skip_bots`, `skip_empty_body`); there is no minimum
length filter.

Short segments (at most `short_segment_verbatim_max_chars`, default 200, an unvalidated starting value)
use the existing verbatim ingestion mode: the stored memory is the exact authored text. Length is decided
on a separate NFC-normalized copy; the stored text is never replaced by it.

## C2 Discovery per artifact type

Issues: `state=all`, ascending by update, pull requests filtered out. Issue and review comments: the
repo-wide comment feeds with `since`. Pull requests: listed newest-update first down to the window floor.
Reviews and PR-level comments: no assumption that a review changes the PR's timestamp; touched PRs are
always read, and a durable reconciliation sweep revisits every PR inside the declared import scope (open
and closed) in bounded batches. Commits: the cursor is the default-branch head SHA, new work is the
`compare` set difference (so merged branches with old author dates are found); a non-ancestor previous head
is a rewrite handled by a bounded walk with SHA-identity dedup. A run fixes `window_high` at its start;
the next window starts at the previous `window_high` minus a 5 minute overlap.

## C3 Durable continuation

Per-scope state stores phase, window, resume key and `listing_complete`. The per-run limit counts
handoffs. The resume key is written in the same transaction as the handoff it describes. A run that hits
the limit ends `continuing` and the next run starts after the key. Scopes are served least-recently-
progressed first with a per-scope quota, so a large scope cannot starve a small one.

## C4 Discovery is separate from downstream recovery

Discovery advances only after a durable handoff or a recorded quarantine, never past an unrecorded
failure. Downstream failures (extraction, publication) never move it and stay recoverable by a recovery
sweep run at the start of every sync and tick: re-dispatch pending documents, re-queue transient failures
below the attempt limit with backoff, recover stale processing. Pre-commit handoff failures are tracked in
`connector_work_items`; after 3 attempts an item is quarantined (visible, counted, re-armable).

## C5 Lease fencing

`connector_configs.fence` increases on every lease acquisition and on disconnect. Every sync write runs in
a transaction that first renews the lease conditioned on `fence = token AND status = 'active'`; no row means
the worker lost the lease and stops without effect. A superseded worker cannot create documents, advance
keys or touch the log.

## C6 Revision publication

`artifact_heads(latest_seq, published_seq)` serializes publication. A changed artifact gets a new
`revision_seq` atomically at handoff. Publication locks the head row, then: an older revision publishes
nothing; the latest revision with complete extraction publishes and retires the previous revision's
machine-only memories; incomplete extraction publishes nothing and is retried (prior revision stays live);
a complete extraction with zero facts publishes nothing, keeps the prior memories and counts a warning;
an already-published revision is a no-op.

## C7 Deletion is never inferred from a bare 404 or an incomplete inventory

A single 404 means unavailable (GitHub hides inaccessible resources the same way). Retirement for deletion
requires HTTP 410, or two consecutive complete inventories (pagination exhausted, no error, no cap or
rate-limit, repository still accessible) that both omit the artifact. Any incomplete scan retires nothing.

## C8 Disconnect covers queued ingestion and publication

Disconnect increments `generation` and `fence` and clears the schedule in one transaction. The ingestion
worker re-checks status and generation before any provider call and again inside the publication
transaction (`FOR SHARE` on the connector row); a mismatch publishes nothing and fails the document as
`connector_disconnected`. Removing imported memories is optional and follows C9.

## C9 Human-edited lineages are preserved

Source refresh, deletion and remove-on-disconnect retire only machine-only memories (an unedited single
row). A lineage containing a human edit stays searchable and is flagged `source_stale_at` /
`source_stale_reason`. Attribution and contribution rows are never altered.

## C10 Lock order and edit concurrency

Order: connector row `FOR SHARE`, then `artifact_heads` `FOR UPDATE`, then memory rows `FOR UPDATE` in
ascending id order. An edit locks only its lineage head and revalidates after the lock. Retirement re-reads
the lineage after locking to classify it. If retirement commits first, the edit receives HTTP 409
`source_retired` with the reason and a possibly empty list of candidate replacement memories from the new
revision; the draft is kept; the server and client never choose a replacement. Re-applying requires an
explicit target from the returned candidates. The re-apply records its edit event under the acting editor,
preserves the target's existing contribution history, and keeps the existing attribution guards.

## C11 Repository authorization

Connecting requires that the selected repositories are within BOTH the installation's access and the
connecting user's own access to that installation (the GitHub App user access token reaches only that
intersection; it is used once and discarded). Before every content-fetch batch a supported, current access
check must pass. There is no grace window and a historical connect ticket never establishes continued
access. If the mechanism is unverified, unavailable or denies, the scope pauses and no content endpoint is
called. States: `authorization_unverified` (could not verify), `reauthorization_required` (definitive
denial), `access_lost` (installation no longer lists the repository). Denial and unavailable verification
are reported differently; neither permits an import. The supported mechanism for the background check is
not established by GitHub's documentation and is pending live verification; until the denial semantics are
verified (`authorization_denial_semantics_verified`, default false) even an explicit denial is reported as
`authorization_unverified`.

## Bounds

At most 5 repositories per connector; initial import limited to 90 days and 500 items per type per
repository; at most 300 handoffs per run; at most 12 chunks and 40,000 characters per artifact; extraction
concurrency 4; a per-workspace daily extraction-call budget (default 600) that pauses the connector when
reached. Estimated cost is reported separately from these enforced caps.

## Package interfaces (fixed so the packages can be built in parallel)

**Handoff (`receive` in `services/ingestion/receiver.py`, additive keyword arguments).**
`artifact_link` additionally accepts `source_revision` (sha256 hex of the exact segment text),
`connector_id`, `connector_generation`. New keyword arguments: `context: dict | None` (contextual
labels; stored in `pipeline_data["context"]`, passed to extraction as a separate non-authored field,
never part of the stored text) and `pre_commit: Callable[[AsyncSession], Awaitable[None]] | None`.
`pre_commit` runs inside the handoff transaction after the Document, ArtifactLink and head row are
written and before the commit; if it raises, nothing is committed (this is where the sync orchestrator
renews its lease fence and writes the discovery position, so both are atomic with the handoff).
With `connector_id` set, the receiver (a) salts the content hash with the source identity so identical
text from two sources keeps both provenance rows, (b) locks `artifact_heads` for the artifact, returns
`already_exists=True` with `reason="same_revision"` when the latest link's `source_revision` equals the
new one, otherwise allocates `revision_seq = latest_seq + 1` and sets `documents.supersedes_document_id`
to the published (else latest) previous document. Return dict additionally carries `revision_seq`.

**Failure codes (`documents.pipeline_data["error_code"]`, set with `ingestion_status = failed`).**
`transient` (provider/infra, eligible for the recovery sweep), `permanent`, `budget` (daily extraction
budget reached; retried only after the budget window resets), `connector_disconnected` (C8; re-armable
only after reconnect), `incomplete_extraction` (C6c, eligible like `transient`). Attempts live in
`pipeline_data["attempts"]`, the earliest retry in `pipeline_data["next_retry_at"]` (ISO 8601 UTC).
A completed document that published nothing records `pipeline_data["publication"]` as one of
`published`, `superseded_before_publish`, `no_facts`, `noop`.

**Recovery (`requeue_transient_failure` in the receiver module).**
`requeue_transient_failure(session, document_id, workspace_id, *, now)` resets an eligible failed
connector document to `pending`, increments `attempts`, sets `next_retry_at` by exponential backoff,
and re-dispatches it through the existing outbox. It refuses (returns False) when attempts reached
`connector_max_handoff_attempts`, the error code is not eligible, or the connector is no longer active at
the document's generation.

**Retirement.** `services/memory/retire.py::retire_document_memories` (signature in the stub) is called
by publication (reason `source_superseded`), by the connector-removal path (`connector_removed`) and by the
re-scan deletion rule (`source_deleted`).

**Sync orchestration.** The orchestrator owns lease acquisition, fence checks, cursor and work-item
writes, the recovery sweep and the deletion rule; it consumes `ScopeRun` objects and a
`RepoAccessVerifier` from `connectors/github/contracts.py` and never calls GitHub endpoints directly.

## Implementation status and deviations from the rules above

These are the places where the implementation is narrower than the contract. Each is deliberate
and safe-by-default; none is hidden.

* **Deletion detection is off (C7).** The orchestrator implements the two-complete-inventory rule,
  but it reads `ScopeResult.full_inventory`, and discovery imports a bounded window (days and item
  caps), so it never reports a full inventory. A window that was fully read is not an inventory of
  the source; treating it as one would retire every unchanged artifact after two scans. Until a
  full-listing mode exists, a source deleted on GitHub keeps its memories, with the original source
  link. A 410 on a direct refetch is also not used yet.
* **Recovery sweep runs at the start of every sync run (C4), not on a separate tick.** A scheduled
  run happens at least every interval for an active connector, and a paused or disconnected
  connector must not be retried anyway. Stale `processing` documents are recovered by the worker's
  own redelivery handling, not by the sweep.
* **Scheduling identity.** The tick runs the sync as the connector creator, or as an active
  owner/admin when the creator left (`sm_list_due_connectors`); the orchestrator then records
  `owner_inactive` and stops. No policy was loosened and no privileged write exists.
* **Review sweep ETags are not persisted.** The sweep re-reads every PR in scope each cycle; it is
  correct but not cheap.
* **Per-type import caps are per run.** Continuation runs restart the counter.
* **Short segments use the existing verbatim mode,** so a short authored segment becomes one memory
  with the exact text. The 200-character threshold is an unvalidated starting value, not a quality
  claim.
* **Access verification is not established against live GitHub (C11).** The verifier makes one extra
  call (`GET /user/{id}`) to turn the stored numeric id into a login; whether an installation token
  may call it, and the permission endpoint semantics, are pending live verification. Any failure
  reports `authorization_unverified` and nothing is imported.
