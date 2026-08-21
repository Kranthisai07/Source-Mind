# ADR-010: Conflict Resolution — No AI Ranking

**Status:** Accepted
**Date:** 2026-08-21
**Supersedes:** ADR-008 (Conflict Resolution — AI Advisory Only)

> ADR-008 is no longer in the working tree; it was removed with the rest of
> `docs/architecture/` during a docs cleanup. Its text remains in git history
> at `docs/architecture/ADR-008-conflict-resolution-ai-advisory-only.md`.

---

## Decision

ADR-008 allowed Claude to write a non-binding suggestion into
`memory_conflicts.suggested_resolution` on first read of a conflict, on the
reasoning that "the AI suggestion is shown as advisory in the UI — reviewers
are not anchored to it." That assumption does not hold. Since Phase 1.5,
resolution is restricted to workspace owners and admins, so the suggestion is
shown to exactly one person: the one who can act on it. Anything the model
writes on that screen functions as a lead rather than as context, and the
implementation had in any case drifted well past what ADR-008 permitted —
asking which statement was "more specific and evidence-based" and returning
`kept_a`/`kept_b` with a confidence score. Phase 2 rewrote that prompt to be
neutral, which was insufficient, because the mechanism is the problem and not
its wording. The generation call, its prompt, and the `suggested_resolution`
response field are therefore removed outright rather than neutered. What a
reviewer sees in their place is the neutral `conflict_summary` produced at
detection time — what each side claims and what would settle it, with no
ranking — plus whatever cross-verification they choose to run on demand. No
model output ranks the two claims, at detection or at resolution. A human
resolves the conflict, and the system's job is to describe the disagreement
accurately, not to lean on the outcome.

## Consequences

- `GET /v1/conflicts/:id` no longer takes an Anthropic client, makes no model
  call, and returns no `suggested_resolution`. The endpoint is now a pure read,
  which also removes the ~300–500 ms first-view latency ADR-008 accepted.
- The `memory_conflicts.suggested_resolution` column is retained but is neither
  read nor written. Dropping it requires a migration; keeping it preserves
  historical rows for audit.
- Reviewers lose the "clear-cut conflict" shortcut ADR-008 valued. This is the
  intended trade: a shortcut that works by nudging toward an answer is the
  thing being removed.
- Enforced by `tests/unit/conflict/test_neutral_detection.py`, which asserts the
  absence of the machinery — not merely the wording of a prompt — and which is
  itself checked against the pre-Phase-2 prompts so it cannot pass vacuously.
