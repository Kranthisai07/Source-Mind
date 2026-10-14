# Friends release — deployment record (2026-10-13/14 UTC)

Release SHA: `06e67a85cec7074557e69831b645362f00c2981a` (`main`, merge of #24). PRs merged in order: #20, #21, #23, #25, #22, #24. Draft #26 was a comparison candidate and was closed unmerged (its tree differs from `main` only by 7 blank/`---` lines in `DECISIONS.md`).

## What is deployed

| Component | Version |
|---|---|
| API (`Source-Mind`) | deployment `164eb22d-b2b5-4bfa-ba46-6a17f1ae4ea1` at the release SHA |
| Worker (`Source-Mind-Worker`) | deployment `500d57b9-6a53-4701-87a3-edbfd4a969c5` at the release SHA |
| Frontend | `gh-pages` `50271d6`, bundle `main.4b46eca6.js` (sha256 `fed235c05a233df278409443cbf8ac0e74a207ff72701f68dbf6f0eb29d4198e`), normal mode, real API |
| Database | `sourcemind_release_0009`, Alembic `20261005_0010` -> `20261010_0011` |

Both services are connected to a pinned `commitSha`; their `deploymentTriggers` lists are empty (no push-to-deploy).

## Rollback targets (retained by Railway for roughly 72 h on this plan)

- API `d84f7be6-abbe-47f7-aed5-b4573f9cf093`, worker `0a4d03e7-c11a-4365-8686-1d7e797c0b91`, both commit `797521522aec72d3efc13d0c5aab71cd3ede246e`.
- Frontend: `gh-pages` `11886ddad03591d5116c8bc6006a8f164119d7f2`.
- Migration `20261010_0011` is never downgraded (additive; the two new tables hold 0 rows).

## Sequence and gates (all verified on the day)

1. Image gate, environment health, staged-changes empty, database identity (`IDENTITY_PASS`, eleven checks).
2. Snapshot-consistent backup (`pre-0011`), restored into a disposable PostgreSQL 18 cluster: manifest checksums match, per-table digests equal across 19 tables (`DIGEST_COMPARE_PASS`). Known drift: pgvector 0.8.6 (production) vs 0.8.7 (disposable).
3. Migration dry run, then `--execute` (3 s lock timeout): `MIGRATION_DONE`, then `POST_MIGRATION_PASS` (no table content changed after the snapshot).
4. Old worker warm-stopped; new worker deployed only after the four-part proof (below).
5. API deployed, validated (`/health` 200, unauthenticated `/v1/workspaces` 401, CORS preflight 200).
6. Frontend published with a lease-conditioned push; downloaded files pass the bundle check and match the build hash.

## Old-worker stop proof: manual Railway API substitution

`drain/worker_shutdown.sh` runs inside WSL and its Railway proof calls the `railway` CLI, which is installed on Windows only. The tool reported `railway_running=unavailable`, so it was run with `--no-railway-check`, which can only end in `PARTIAL_PROOF` (exit 3, not success). The Railway half of the proof was taken manually through the Railway API (MCP `environment-status`, `replicaStatus.running`) at two points:

- after the warm shutdown broadcast: `running: 0`, `total: 1` (Ping nodes 0, Redis consumers 0, unacked 0 from the tool in the same window);
- again about 15 s later, and a second tool run with `--expect-nodes 0`: ping and consumer proofs still true, `running: 0`.

The same substitution applied to `--validate-new`: the tool's five local checks were true; its Railway check was `false` only because it had been disabled. The manual read was MCP `list-deployments`: newest worker deployment `500d57b9`, `SUCCESS`, `meta.commitHash` equal to the release SHA, and `get-service-config` equal to the earlier copy except for the added pinned `commitSha`. Follow-up: make the tool accept the Railway state from a file or run the CLI from Windows, so the proof is single-step.

## Rehearsal results (real Clerk, real providers)

Account A (`kranthisaigadi007@gmail.com`, resolved to the existing user) and account B (`kranthigadi26@gmail.com`, new user) in workspace `release-rehearsal`: create workspace, ingest (3 memories in 29.3 s), edit (version 2, 26.5 s), search with attribution, add member through real Clerk, B sees only the shared workspace, B's direct reads of the Production workspace id all return 404, B searches the shared workspace.

### Search results and memory versions

Search returned two hits for "shared store" and this was first read as a stale old version next to the new one. The database says otherwise:

| Memory | version | `parent_memory_id` | `current_version` | Role |
|---|---|---|---|---|
| `954597dd` | 1 | none | false | superseded v1 of the edited fact |
| `65e9fba3` | 2 | `954597dd` | true | current v2 of the edited fact |
| `2c5871af` | 1 | none | true | a separate extracted fact (same sentence start, "...supports vector search") |
| `4e925252` | 1 | none | true | a third extracted fact |

Across four queries in hybrid, semantic and keyword modes (12 searches) the superseded `954597dd` never appeared and no result carried `current_version=false`. The two "shared store" hits are the edited fact (v2) and a different, near-duplicate extracted fact. So search does not return superseded versions. The near-duplicate comes from extraction turning one sentence into several overlapping facts, which the UI already warns about; no code defect was found.

## Provider use

One extraction submission (Sonnet) plus embeddings for 3 memories and one edit; well under the stated $0.25 ceiling.

## Closeout items

- +65-minute stranded-job check, run at 2026-10-14T01:23Z (about 89 minutes after the old worker stopped; the stop was a warm shutdown, not a kill, so nothing was expected to be stranded): Redis `unacked` hash and index empty, all priority queues empty, Celery active/reserved/scheduled empty (`DRAIN_PASS`, 1 node), 0 documents not in `completed`/`failed`, no connector syncs or handoffs in progress. No stranded job.
- Rehearsal workspace and backup artifacts are intentionally retained.
