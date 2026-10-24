# Production window record, 2026-10-20 to 2026-10-24 (migration 0014 and release `468d7b8`)

Decision and rationale: D-051 in `docs/architecture/DECISIONS.md`. This file is the timeline and the evidence, with counts and statuses
only. No credential value appears here or was read while preparing it.

## Outcome (verified 2026-10-24 17:11 UTC)
- api deployment `f5fc5201` and worker deployment `ce5a4f41`, both `SUCCESS` on `468d7b8dbe57759486cdb4d97e8c55426c6f10f7`.
- `/health` 200 (PostgreSQL and Redis healthy), `/mcp` 404 from the app, `/v1/workspaces` 401, OAuth metadata 404.
- MCP and the connector scheduler were not enabled. No Railway SSH key is registered. No public TCP proxy exists on Postgres or Redis.
- The api was offline from 2026-10-21 02:16:44Z to about 2026-10-24 17:09Z (about three and a half days). No real users were affected;
  none had been invited.

## Timeline (UTC)
| When | Event |
|---|---|
| 10-20 00:15 | Backup `20261020T001502Z-pre-0012` taken and restore-verified (21 tables, equal digests). |
| 10-20 ~00:50 to 05:49 | The api could not reach its database: its `DATABASE_URL` pointed at the public Postgres proxy that had just been deleted. `/health` returned 503 with PostgreSQL unhealthy. Found by the identity check at about 05:3x; start time is approximate. |
| 10-20 05:38 to 05:49 | `SOURCEMIND_RUNTIME_DATABASE_URL` repointed to the private address by the owner; Postgres redeployed (`db1d3b6a`), api (`354a99ac`) and worker (`74ea3fa1`) redeployed on the old code; `/health` 200. |
| 10-21 02:16:44 | api stopped (T0). Drain proofs passed twice (queues empty, no unacked messages, zero pending documents, syncs or handoffs). |
| 10-21 ~02:23 to 02:27 | Old worker warm-stopped: 0 nodes, 0 consumers, 0 unacked; dashboard card "Completed". |
| 10-21 02:28 | Final backup `20261021T022812Z-pre-0012-final` taken with writers stopped; restore verified. |
| 10-21 02:29:57 | Migration `20261010_0011` to `20261016_0014`, one attempt, `MIGRATION_DONE`; `POST_MIGRATION_PASS` (20 checks). |
| 10-21 ~02:35 | Every deploy refused: "Deploys have been paused temporarily". Railway incident QH8L8B3E (builds paused, API and dashboard degraded) was posted from 07:24 and resolved later. |
| 10-20 13:58 to 10-22 03:32 | Attack window Railway reports in its preliminary report; Railway recommends rotating all keys stored on Railway. |
| 10-24 15:29 | Postgres proxy (`trolley`) and Redis proxy (`shinkansen`) removed and the changes applied; a protocol greeting then got no answer from either. |
| 10-24 | Owner rotated Clerk, OpenAI, Anthropic, Redis and database passwords in Railway. Database role passwords were then set with `ALTER ROLE` in the container; network login with each new password worked, a wrong one was refused. |
| 10-24 16:50 and 16:58 | Worker `ce5a4f41` and api `f5fc5201` created, pinned to `468d7b8`. The worker before that (`30d2bf21`) was already on `468d7b8`; it was retrying a stale Redis password and was redeployed. |
| 10-24 17:11 | Probes above. |

## Rollback position
The previous deployments (`354a99ac` api, `74ea3fa1` worker on `06e67a8`) remain redeployable, but they would run on the migrated schema, which
was tested locally only. Backups `20261020T001502Z-pre-0012` and `20261021T022812Z-pre-0012-final` are kept locally (they contain user data) under
`D:\SourceMind-Operationsriends-releaseackups\`.

## Not verified
Contents of Railway variables (only names and match results were used), that GitHub App and Slack credentials were rotated, behaviour under real
user load, and whether anything read the database during the exposure window (no log drain or Sentry was checked; Railway's report is preliminary).
