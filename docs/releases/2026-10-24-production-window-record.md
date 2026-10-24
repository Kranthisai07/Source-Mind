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

## Addendum 2026-10-24 (evening): frontend publish, Clerk key mismatch, deployment pin

**Frontend published.** `gh-pages` moved from `50271d603aeb58b5cb12ce86795c5b695fe513ba` to `cf31be7ee214beab62518e51c768f5f70ff7cc01` (lease-guarded push), built from `468d7b8`.
Bundle `main.095c4600.js`, sha256 `517e156d7f0965c146817685da99869c97e08406b919d5a7e899f5348398f0b3`; the file GitHub serves has the same hash. `check_bundle.sh` passed 10 of 10 on the
build and again on the downloaded live files; no secret-key literal; `404.html` identical to `index.html`; `/dashboard` returns the app body with the Pages fallback status 404.
Rollback: `git push --force-with-lease=refs/heads/gh-pages:cf31be7... origin backup/gh-pages-50271d6:refs/heads/gh-pages` (local tag).

**Clerk secret key mismatch after the rotation.** On the published site every signed-in request failed (`401 SM001 "Unable to verify the Clerk user profile."`) although the browser held a valid
Clerk session. Checking each service's `CLERK_SECRET_KEY` against Clerk's own API (kind, length and HTTP status only, no value printed): api returned 401, worker returned 200 (both test keys, 50
characters). The api held a different, invalid value. The owner copied the worker's value to the api; afterwards both returned 200 and `/v1/workspaces` returned 200 with two workspaces.
Sign-in was broken from at least the api deployment of 2026-10-24 16:58Z until the fix deployed at 19:57Z; the time the api's variable was changed is not known.

**Browser checks after the fix** (Chrome, signed in; browser evidence): dashboard shows real Production data (5 memories, 1 contributor, 0 open conflicts); Settings > API Keys shows
"MCP is not enabled on this deployment, so keys cannot be used yet"; Connectors shows 0 sources; `GET .../connectors/github/authorize` returns 503 "GitHub connection is not configured" as expected.
Not checked: the Contributors page (it needs a connector, `/connectors/:id/contributors`) and the `[SourceMind] API mode: REAL` console line (not captured).

**Deployment pin not kept.** The api deployment created by the variable change (`5c64439b`, 2026-10-24 19:57:20Z, reason `deploy`) is on `ed21410`, the head of `main`, not the pinned `468d7b8`. `main` differs from
`468d7b8` by three documentation files only. The worker is still `ce5a4f41` on `468d7b8`. No deployment followed the merges of #35 and #36, so no auto-deploy trigger is evidenced; a
deploy started by a variable change followed the branch head. Hypothesis, not confirmed.

**Re-pinned and verified (2026-10-24 20:27Z).** A later session ran `connect-service-source` on the api with `commitSha` `468d7b8dbe57759486cdb4d97e8c55426c6f10f7`. Railway's config still showed that pin before the re-pin, so the
deploy that followed the variable change (`5c64439b`, `ed21410`) did not honour it. New api deployment `e726dcf8` (20:18:52Z) is `SUCCESS` on `468d7b8`; `5c64439b` is REMOVED. Verified afterwards: `/health` 200 with
PostgreSQL and Redis healthy, `/v1/workspaces` without a login 401, `/mcp` 404, OAuth metadata 404, the api's Clerk key 200 from Clerk, worker `ce5a4f41` still `SUCCESS` on `468d7b8`. `/health` reports
`environment: staging` in production (unchanged, listed as open in D-051).
