# Pilot Search Evidence Readiness Note

## Implemented in this change

- Migration `20261010_0011` adds workspace-scoped, FORCE-RLS-protected,
  append-only `search_events` and `search_ratings` tables. The runtime role gets
  only `SELECT` and `INSERT`; downgrade refuses to remove non-empty evidence.
- Evidence mode is off by default. When enabled, a successful search flushes one
  event before the response is returned. A failed evidence write fails the
  request; rejected filters and failed searches do not create events.
- The event stores the query, complete request parameters, request ID, ordered
  response snapshot, memory and document IDs, returned provenance and scores,
  and the frozen search algorithm identifier.
- An admin/operator endpoint records independent, self, or external ratings.
  It validates workspace/event/result membership, recorded edit history, rater
  identity rules, and workspace-scoped idempotency.
- Pilot export schema version 2 includes documents, all memory versions,
  attribution snapshots and edits, relations, conflicts, membership, search
  events and ratings. A deterministic SHA-256 covers the canonical payload;
  export time is deliberately excluded.

## Remaining pilot gates

- Apply the migration in an isolated pilot environment with the restricted
  runtime role configured, then enable `PILOT_SEARCH_EVIDENCE_ENABLED=true` for
  both the API process configuration and the pilot rehearsal only.
- Rehearse operator rating ingestion and export from the isolated workspace.
  There is intentionally no public participant rating UI in this change.
- Verify pseudonymous participant setup, workspace isolation, verbatim memory
  creation, search provenance, update-relation visibility, and the Q5
  non-contributor export before invitations.
- Freeze the held-out manifest and rating instructions before revealing scores.
  This implementation captures evidence; it does not change candidate formulas,
  choose a scorer, establish attribution accuracy, or authorize a pilot start.
