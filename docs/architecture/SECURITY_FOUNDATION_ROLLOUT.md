# Security Foundation Rollout

**Status:** implemented locally on 2026-09-09. Do not call this production
verified until the disposable PostgreSQL test and the production caller matrix
below both pass. No live database, Slack workspace, customer data, or paid API
was used while implementing this change.

## Implementation map

| Boundary | Primary paths/symbols |
|---|---|
| Clerk session verification | `apps/api/sourcemind/core/dependencies.py::_verify_clerk_token`, `get_current_user` |
| Active membership and roles | `apps/api/sourcemind/core/dependencies.py::require_workspace_permission` and resource helpers |
| Membership revocation | `apps/api/sourcemind/api/v1/workspaces.py::revoke_workspace_member` |
| Route enforcement | `apps/api/sourcemind/api/v1/{analytics,conflicts,connectors,memories,search,team,workspaces}.py` |
| Transaction RLS context | `apps/api/sourcemind/core/database.py::set_rls_user_context`, `set_rls_workspace_context` |
| RLS policy migration | `apps/api/alembic/versions/20260908_0006_security_foundation.py` |
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

This procedure accepts only the exact local runtime target
`sourcemind_test@127.0.0.1:55432/sourcemind_security_test`. The database and
Redis data directories are tmpfs. Never substitute a live URL.

```powershell
docker compose -f infra/docker-compose.security-test.yml up -d --wait

Push-Location apps/api
$env:ENVIRONMENT = 'development'
$env:DEBUG = 'false'
$env:AUTH_DEV_BYPASS_ENABLED = 'true'
$env:DATABASE_URL = 'postgresql+asyncpg://sourcemind_owner:sourcemind_owner@127.0.0.1:55432/sourcemind_security_test'
& .\.venv\Scripts\alembic.exe upgrade head

$env:SECURITY_TEST_ALLOW_DISPOSABLE = '1'
$env:TEST_DATABASE_URL = 'postgresql+asyncpg://sourcemind_test:sourcemind_test@127.0.0.1:55432/sourcemind_security_test'
$env:REDIS_URL = 'redis://127.0.0.1:56379/15'
& .\.venv\Scripts\python.exe -m pytest tests/integration/test_security_foundation_real_db.py -o addopts='' -q
Pop-Location

docker compose -f infra/docker-compose.security-test.yml down -v
```

The populated test creates:

1. Caller A, an active owner of workspace A, and a real memory in workspace A.
2. Caller B, an authenticated user with zero memberships.
3. Caller C, an active member of workspace B.
4. A revocation of caller A after the authorized read.

Required result: A sees the populated memory before revocation; B and C see no
row and receive the resource 404; A sees no row after revocation. The runtime
role must be `NOSUPERUSER NOBYPASSRLS`, and the migration/table owner must be
a different role.

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
  `20260908_0006`; the stronger RLS policy can safely remain during diagnosis.
- If a database downgrade is required, run
  `alembic downgrade 20250817_0005` as the migration owner before starting the
  old application. The downgrade restores the exact legacy policies on tables
  that previously had RLS and disables RLS only on tables newly protected by
  this migration.
- Re-run the role/ownership query after either direction. Never leave the
  migration-owner credential in API or worker configuration.

## Required owner action

`apps/api/sourcemind-kranthi.2026-04-14.private-key.pem` exists locally and is
git-ignored, untracked, and absent from repository history. The narrowed
development mounts and Docker ignore rules now keep it out of containers, but
code cannot prove whether the key is active. The owner must identify its
provider, rotate/revoke it if active, verify the replacement, and only then
remove the local file.

## Local evidence and remaining blocker

- Full unit suite: 396 passed, 7 skipped.
- Changed-file Ruff check: passed.
- `uv lock --check`: passed after removing Playwright and repairing prior
  lock drift.
- Alembic: one head; upgrade and downgrade SQL both compile offline.
- Mutation proof: disabling issuer verification made
  `test_real_session_token_rejects_wrong_issuer` fail; bypassing the
  owner-target check made `test_admin_cannot_begin_an_owner_departure` fail.
  Both fixes were restored.
- Real populated PostgreSQL/RLS test: written but not run here because neither
  Docker nor `pg_ctl` is installed. Production deployment is blocked until
  the disposable procedure above passes.
