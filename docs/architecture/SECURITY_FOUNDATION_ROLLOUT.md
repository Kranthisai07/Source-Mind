# Security Foundation Rollout

**Status:** local verification expanded on 2026-09-13. The 0007 migration,
disposable PostgreSQL RLS coverage, signed authorization/revocation acceptance,
Redis acceptance, and unit suite have passed locally. This is not production
verification: no production database, Railway service, Slack workspace, customer
data, paid API, deployment, or push was used.

## Implementation map

| Boundary | Primary paths/symbols |
|---|---|
| Clerk session verification | `apps/api/sourcemind/core/dependencies.py::_verify_clerk_token`, `get_current_user` |
| Active membership and roles | `apps/api/sourcemind/core/dependencies.py::require_workspace_permission` and resource helpers |
| Membership revocation | `apps/api/sourcemind/api/v1/workspaces.py::revoke_workspace_member` |
| Route enforcement | `apps/api/sourcemind/api/v1/{analytics,conflicts,connectors,memories,search,team,workspaces}.py` |
| Transaction RLS context | `apps/api/sourcemind/core/database.py::set_rls_user_context`, `set_rls_workspace_context` |
| RLS policy migrations | `apps/api/alembic/versions/20260908_0006_security_foundation.py`, `apps/api/alembic/versions/20260909_0007_rls_context_hardening.py` |
| Queued reauthorization | `apps/api/sourcemind/workers/ingestion.py`, `connector_tasks.py` |
| Slack identity and transport | `apps/api/sourcemind/services/slack/bot.py`, `apps/api/sourcemind/api/v1/slack.py` |
| URL SSRF boundary | `apps/api/sourcemind/core/url_security.py`, `apps/api/sourcemind/services/ingestion/{receiver,extractor}.py` |
| Rate limits | `apps/api/sourcemind/core/rate_limit.py` and protected route call sites |
| Connector redaction | `apps/api/sourcemind/schemas/connector.py`, `apps/api/sourcemind/connectors/github/connector.py` |
| Deployment freshness | `apps/api/sourcemind/core/runtime_state.py`, `apps/api/sourcemind/api/v1/health.py` |
| Disposable RLS test | `apps/api/tests/integration/test_security_foundation_real_db.py` |

## Authorization policy

Only an active membership on a non-deleted workspace grants access.
`departing`, `departed`, or a non-null `departed_at` revokes access
immediately.

| Capability | viewer | member | admin | owner |
|---|---:|---:|---:|---:|
| Read workspace data, memories, conflicts, analytics, and search | yes | yes | yes | yes |
| Create memories and update memories | no | yes | yes | yes |
| Delete memories and review/resolve conflicts | no | no | yes | yes |
| Create, inspect, update, delete, or sync connectors | no | no | yes | yes |
| Initiate, assign, complete, or list handoffs | no | no | yes | yes |
| Revoke a member or admin | no | no | yes | yes |
| Revoke an owner | no | no | no | yes |

The final active owner cannot be revoked. Resource-ID routes return the
resource's 404 to outsiders; workspace-ID routes return SM022/404. A real
member with an insufficient role receives SM005/403.
Although admins can manage ordinary handoffs, only an owner can initiate
another owner's departure.

Workers repeat authorization at execution time. A queued ingestion or connector
sync accepted before revocation must not run after that membership is revoked.

## Production authentication

- `AUTH_DEV_BYPASS_ENABLED=false` outside explicit local development.
- Set `CLERK_SECRET_KEY`, `CLERK_PUBLISHABLE_KEY`, and a non-empty JSON
  `CLERK_AUTHORIZED_PARTIES` allowlist of exact frontend origins.
- Set `CLERK_AUDIENCE` only for a custom Clerk JWT template that emits
  `aud`; ordinary Clerk session tokens do not require it.
- Session verification requires RS256, `kid`, `sub`, `sid`, `iss`,
  `iat`, `nbf`, and `exp`; validates issuer, signature, time claims,
  optional audience, and `azp` when present; rejects pending sessions.
- A missing or invalid Clerk user profile fails closed and cannot create a
  fabricated local identity.

The implementation follows Clerk's
[session-token claim reference](https://clerk.com/docs/guides/sessions/session-tokens),
[backend token verification guidance](https://clerk.com/docs/reference/backend/verify-token),
and [manual verification checks](https://clerk.com/docs/guides/sessions/manual-jwt-verification).

## Slack boundary

Slack memory commands are disabled unless
`SLACK_MEMORY_COMMANDS_ENABLED=true`. Enabling also requires
`SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, and an operator-owned mapping:

```json
{
  "T123": {
    "workspace_id": "11111111-1111-4111-8111-111111111111",
    "allowed_channel_ids": ["C123"],
    "users": {
      "U123": "22222222-2222-4222-8222-222222222222"
    }
  }
}
```

The Slack team, channel, and user must all match exactly. The mapped SourceMind
user must still hold active membership when the command runs. HTTP delivery
uses Bolt's `AsyncRequestVerification`; its HMAC verification accepts a
current legitimate signature and rejects a forged signature or a correctly
signed request replayed outside Slack's five-minute window. Slash-command
responses are ephemeral. App mentions never return memory content.

## URL ingestion boundary

URL ingestion is disabled unless `URL_INGESTION_ENABLED=true`. When enabled:

- Only HTTP/HTTPS with default ports and no embedded credentials is allowed.
- Local/internal hostnames and every non-global IPv4/IPv6 address are rejected.
- Every DNS answer must be public, and the validated addresses are pinned for
  the connection to resist DNS rebinding.
- Every redirect target is re-resolved and revalidated.
- Proxy environment variables, cookies, browser execution, scripts, and
  subresource loads are disabled.
- Time, redirect count, response bytes, status, and content type are bounded by
  `URL_INGESTION_TIMEOUT_SECONDS`,
  `URL_INGESTION_MAX_REDIRECTS`, and
  `URL_INGESTION_MAX_BYTES`.

## Expensive-operation limits

Redis uses one atomic INCR/EXPIRE Lua operation per user/workspace/operation.
The default posture is fail closed when Redis is unavailable:

| Environment variable | Default |
|---|---:|
| `RATE_LIMIT_SEARCH_PER_MINUTE` | 60 |
| `RATE_LIMIT_INGESTION_PER_MINUTE` | 10 |
| `RATE_LIMIT_ANALYTICS_PER_MINUTE` | 30 |
| `RATE_LIMIT_WORKSPACE_CREATE_PER_HOUR` | 5 |
| `RATE_LIMIT_CONNECTOR_SYNC_PER_HOUR` | 5 |
| `RATE_LIMIT_SLACK_PER_MINUTE` | 20 |
| `RATE_LIMIT_FAIL_CLOSED` | true |

## Disposable verification

Only use a disposable, locally verified runtime target. Never substitute a live
URL, a Railway URL, or an ordinary development service.

`infra/docker-compose.security-test.yml` is the CI topology: PostgreSQL is
published only at `127.0.0.1:55432`, Redis only at `127.0.0.1:56379`, and both
are ephemeral. CI must inject the database credentials instead of copying
credentials from a workstation.

```powershell
docker compose -f infra/docker-compose.security-test.yml up -d --wait

Push-Location apps/api
$env:ENVIRONMENT = 'development'
$env:DEBUG = 'false'
$env:AUTH_DEV_BYPASS_ENABLED = 'true'
$env:SECURITY_TEST_ALLOW_DISPOSABLE = '1'
$env:TEST_DATABASE_URL = $env:SECURITY_TEST_DATABASE_URL
$env:DATABASE_URL = $env:TEST_DATABASE_URL
$env:REDIS_URL = 'redis://127.0.0.1:56379/15'

& .\.venv\Scripts\python.exe -u -m pytest tests/integration/test_security_foundation_real_db.py -o addopts='' -q
& .\.venv\Scripts\python.exe -u -m pytest tests/integration/test_security_acceptance_real_services.py::test_real_redis_counters_scopes_expiry_sharing_and_fail_closed -o addopts='' -q
& .\.venv\Scripts\python.exe -u -m pytest tests/integration/test_security_acceptance_real_services.py::test_signed_api_role_matrix_revocation_and_worker_reauthorization -o addopts='' -q
Pop-Location

docker compose -f infra/docker-compose.security-test.yml down -v
```

Before any test, verify that the PostgreSQL runtime role is
`NOSUPERUSER NOBYPASSRLS`, does not own protected tables, and points to the
expected disposable database. Verify Redis is the intended local database
number. Port numbers alone are not identity proof.

### Recorded WSL verification environment

The successful 2026-09-13 run used a native-Windows Python client with the
services already running in Ubuntu WSL. PostgreSQL listened on
`127.0.0.1:55432`, with its ephemeral data directory at
`/tmp/sourcemind-security-acceptance-e8af933/pgdata` and local admin socket at
`/tmp/sourcemind-security-acceptance-e8af933/socket`. Redis listened on
`127.0.0.1:56379`, database `15`. This is recorded runtime evidence, not a
portable path requirement; revalidate the target and role identity for every
run.

The current WSL cluster did not accept the static owner/bootstrap passwords in
the compose fixture. That was a local configuration mismatch, not a platform
restriction. Credentials are intentionally not recorded here. Fresh and
populated migration verification therefore used the existing WSL PostgreSQL
admin socket, created only named throwaway databases, installed the required
extensions there, rendered Alembic SQL offline with `PYTHONUTF8=1` and
`PYTHONIOENCODING=utf-8`, and applied it after `SET ROLE sourcemind_owner`.
Do not treat that local admin path as an API/worker runtime configuration.

### Migration acceptance contract

Run these checks only on newly created disposable databases:

1. **Fresh:** create the required PostgreSQL extensions, apply head, and assert
   Alembic revision `20260909_0007`, the creator column, the bootstrap/access
   grant tables, and forced RLS ownership.
2. **Populated:** apply through `20260908_0006`, create one active owner,
   organization, and workspace, then apply `0007`. Assert the creator is
   backfilled, one active bootstrap/access grant has the owner role, RLS is
   forced, and the runtime role has no direct select privilege on the internal
   access-grant table.

`0007` makes membership/access grants, rather than a caller-supplied workspace
context alone, the authority for workspace and member visibility. The
transaction-local context remains a scope constraint and is not authority.

The populated test creates:

1. Caller A, an active owner of workspace A, and a real memory in workspace A.
2. Caller B, an authenticated user with zero memberships.
3. Caller C, an active member of workspace B.
4. A revocation of caller A after the authorized read.

Required result: A sees the populated memory before revocation; B and C see no
row and receive the resource 404; A sees no row after revocation.

## Production rollout

1. Take and verify a database backup. Drain writes and queued ingestion/sync
   work.
2. Preflight membership data for unknown roles/statuses and ensure at least one
   active owner remains per live workspace.
3. Run Alembic as a migration/table-owner credential, never as the long-lived
   API/worker runtime credential.
4. Confirm the API and worker runtime role is not superuser, cannot bypass RLS,
   and does not own protected tables:

```sql
SELECT rolname, rolsuper, rolbypassrls
FROM pg_roles
WHERE rolname = '<runtime-role>';

SELECT c.relname, pg_get_userbyid(c.relowner) AS owner,
       c.relrowsecurity, c.relforcerowsecurity
FROM pg_class AS c
WHERE c.relname IN (
  'workspaces', 'workspace_members', 'documents', 'memories',
  'attributions', 'attribution_edits', 'memory_relations',
  'memory_conflicts', 'connector_configs', 'connector_sync_logs',
  'artifact_links', 'handoff_records', 'handoff_assignments'
)
ORDER BY c.relname;
```

5. Deploy API and workers with production Clerk settings and both optional
   ingestion integrations still disabled.
6. Before sending application traffic, call `/health`. Require
   `requests_since_start == 0`, record a new `process_instance_id`, and
   independently confirm every old API/worker instance is gone. With multiple
   replicas, inspect each instance directly; one load-balanced response cannot
   prove all replicas are fresh.
7. Verify a normal request increments `requests_since_start`; health probes
   themselves must not increment it.
8. Against a populated disposable production-validation workspace, run every
   workspace/resource route as A, B, and C. Include reads and mutations. A must
   receive real populated data; B and C must receive 404 and perform no write.
9. Revoke A, then repeat direct memory read, search, ingestion, connector sync,
   analytics, conflicts, handoffs, and any already queued ingestion/sync work.
   Every path must fail closed.
10. If Slack is enabled, send one current correctly signed mapped command, one
    forged signature, and one correctly signed stale replay. Only the first may
    reach a handler, and its response must be ephemeral.
11. If URL ingestion is enabled, verify a public text response succeeds and
    localhost, private IP, mixed public/private DNS, credentialed URL,
    non-default port, redirect-to-private, oversized, and timeout cases fail.
12. Monitor 401/403/404/429/503 rates, RLS policy errors, worker revocation
    rejections, Redis failures, and connector redaction before widening traffic.

## Rollback

- First disable `SLACK_MEMORY_COMMANDS_ENABLED` and
  `URL_INGESTION_ENABLED`, stop new ingestion/sync work, and drain workers.
- Prefer an application rollback that remains compatible with migration
  `20260909_0007`; the stronger policy can safely remain during diagnosis.
- If the application requires the pre-0007 schema, first run
  `alembic downgrade 20260908_0006` as the migration owner. Only if reverting
  the D-009 policy foundation as well, follow with
  `alembic downgrade 20250817_0005`. The 0006 downgrade restores the exact
  legacy policies on tables that previously had RLS and disables RLS only on
  tables newly protected by that migration.
- Re-run the role/ownership query after either direction. Never leave the
  migration-owner credential in API or worker configuration.

## Required owner action

`apps/api/sourcemind-kranthi.2026-04-14.private-key.pem` exists locally and is
git-ignored, untracked, and absent from repository history. The narrowed
development mounts and Docker ignore rules now keep it out of containers, but
code cannot prove whether the key is active. The owner must identify its
provider, rotate/revoke it if active, verify the replacement, and only then
remove the local file.

## Local evidence and remaining gaps

- Fresh and populated disposable migration checks passed: both reached
  `20260909_0007`; the populated 0006-to-0007 check verified creator backfill,
  active owner bootstrap/access grants, forced RLS, separated table ownership,
  and no runtime-role select privilege on internal grants.
- Broader real PostgreSQL RLS coverage passed: 6 tests in 4.16 seconds.
- Redis acceptance passed: 1 test in 3.23 seconds. Signed
  authorization/revocation acceptance passed: 1 test in 22.68 seconds.
- The isolated unit suite passed: 398 passed, 1 skipped in 47.96 seconds.
- Repository-wide Ruff is not clean: 50 findings in pre-existing unrelated
  tests. The explicit backend paths also have three findings: one import-order
  issue in 0007 and two S608 findings in migration/seed SQL construction. No
  source changes were made during this verification pass.
- No production caller matrix, deployment freshness check, Slack/URL enabled
  flow, owner secret rotation, production migration, rollback execution, push,
  or deployment was performed. Those remain owner-controlled acceptance gaps.
