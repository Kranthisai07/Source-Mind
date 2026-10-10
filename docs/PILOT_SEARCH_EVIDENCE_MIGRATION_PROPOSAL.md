# Pilot Search Evidence Migration Proposal

The current schema has no `search_events` or `search_ratings` tables. The pilot
export therefore reports both datasets as unavailable and returns empty arrays;
it does not imply that no searches or ratings occurred.

Before collecting search evidence, approve a migration with two workspace-scoped
append-only tables:

- `search_events`: `id`, `workspace_id`, pseudonymous `user_id`, query text or
  approved query reference, requested mode, frozen algorithm ID, ordered memory
  IDs and scores, request ID, and `created_at`.
- `search_ratings`: `id`, `workspace_id`, `search_event_id`, pseudonymous rater
  ID, memory ID, rating value, rating-source class, exclusion reason if any, and
  `created_at`.

Both tables should use UUID primary keys, foreign keys to their workspace and
related rows, workspace indexes, row-level-security policies matching existing
workspace isolation, and no update/delete application path. The export must add
explicit `workspace_id` predicates for both tables. This proposal is not a
migration and creates no tables.
