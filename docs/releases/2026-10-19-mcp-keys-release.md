# Release plan: automatic GitHub ingestion, contributor insights, MCP API keys (PLAN ONLY, not executed)

Nothing in this file has been run against production. Each outward step below needs its own explicit approval
from the owner. Production today: pinned commit `06e67a85cec7074557e69831b645362f00c2981a`, Alembic `20261010_0011`
(see `2026-10-14-friends-release.md`).

## What ships
Automatic GitHub ingestion (migration `0013`), source provenance (`0012`), contributor insights, and the read-only
MCP interface with workspace API keys (`0014`). **MCP stays off**: `MCP_ENABLED` is unset (default false) in production.
Also D-050: keyword search needs no provider key, and exception logs no longer carry local variables.

## Migrations (all additive; reversibility proven in CI and locally 0014 -> 0011 -> head)
| Revision | Change |
|---|---|
| `20261014_0012` | source provenance and stale-source markers |
| `20261015_0013` | automatic ingestion schema |
| `20261016_0014` | `mcp_api_keys`, append-only `mcp_key_events`, FORCE RLS, revocation trigger |

## Pre-checks
1. Production Alembic revision is `20261010_0011`; roles `sourcemind_owner` / runtime exist; `SOURCEMIND_RUNTIME_ROLE` set for the owner-run migration.
2. Snapshot-consistent backup, restored into a disposable PostgreSQL and digest-compared (same procedure as the friends release).
3. Release PR merged by the owner; record the merge commit SHA; CI green on it.
4. Production log review (2026-10-19) found no exception dump in retained logs; credential rotation is the owner's call.

## Order of operations (follows the drain sequence used on 2026-10-13/14)
1. Warm-stop the worker, prove it stopped (`drain/worker_shutdown.sh` plus Railway replica status).
2. Owner-run `alembic upgrade head` (reviewed dry run, then execute with a short lock timeout).
3. Deploy api, then worker, pinned to the merge commit. Do not set `MCP_ENABLED`.
4. Smoke: `/health` 200, unauthenticated `/v1/workspaces` 401, `/mcp` not mounted (404), `/v1/workspaces/{id}/mcp-keys` requires auth.
5. Publish the frontend only after the api is healthy.

## Rollback
Redeploy api and worker at `06e67a85cec7074557e69831b645362f00c2981a` (or the retained deployments, roughly 72 h).
Migrations 0012-0014 add tables and columns, but 0012/0013 also alter live tables and replace constraints. Old code on the new schema was tested locally only (744 unit and 131 integration tests pass; one integration test fails the same way on the old schema), not live. A code rollback after migrating therefore relies on that local evidence; see D-051.
If data is affected, restore from the pre-migration backup. A downgrade to `20261010_0011` is tested but destroys any MCP key data.

## Enabling MCP later (separate approval)
Set `MCP_ENABLED=true`, `MCP_AUTH_MODE=api_key`, `MCP_RESOURCE_URL`; redeploy; create a key in Settings; test a read; revoke it.
Verified so far: local Claude Code and Codex CLI, and Claude Code on a hosted staging environment (since deleted).

## Not covered / unverified
Cursor and Codex panel clients, key expiry live, multiple replicas, remote latency/outage measurements, any production run.
