# Staging deployment (Railway) with MCP API keys enabled

Purpose: run this branch on a **separate, empty Railway environment** and test the MCP key interface over
HTTPS with real Claude Code and Codex, **without touching production**. Rehearsed locally on a fresh
PostgreSQL first, then executed on Railway on 2026-10-19 (see "Lessons from the first hosted run" at the end).

## Ground rules

* **Never point a production service at the `staging/mcp-keys` branch** or copy production variables
  here. Staging gets its own Postgres, Redis and secrets. Create the environment **empty**, not by cloning
  production.
* **No secret goes into chat, a commit or a screenshot.** Passwords and Clerk keys are typed into the Railway
  variables UI (or generated in your own terminal). This document lists variable NAMES only.
* **No AI provider keys** (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) are set: staging makes no model calls.
  Search is keyword-only (`mode: keyword`); the seeded memories have no embeddings.
* Row-level security is the reason for the role setup below. Railway gives you a superuser, and a superuser
  bypasses every policy; the app does not check this at startup. The API must connect as
  `sourcemind_runtime` (NOSUPERUSER, NOBYPASSRLS).

## What you create

| Service | Source | Notes |
|---|---|---|
| `postgres` | Docker image `pgvector/pgvector:pg16` | Needs the `vector` extension. Add a volume. (PG 16 and 18 are both exercised by our CI.) |
| `redis` | Railway Redis | Mandatory (rate limiter, Celery broker). |
| `api` | GitHub repo, branch `staging/mcp-keys` | Root directory `/apps/api`, builder **Dockerfile**, path `/apps/api/Dockerfile.staging` (set in the service settings: Railway no longer accepts config-as-code for new services, so `railway.staging.json` is unused). One replica. |

No worker service in this pass: MCP reads do not need one.

## Step 1: environment and services (Railway dashboard)

1. In the existing project: **Environments > New Environment > Empty**, name it `staging`.
2. Add **Postgres**: *New > Docker Image* `pgvector/pgvector:pg16`. Variables: `POSTGRES_PASSWORD` (a long
   random value; generate it with `openssl rand -hex 24`), `POSTGRES_DB=railway`,
   `PGDATA=/var/lib/postgresql/data/pgdata`. Attach a **volume** mounted at `/var/lib/postgresql/data`.
   Enable a **TCP proxy** for port 5432 (needed once, for the role bootstrap in step 2).
3. Add **Redis** from the Railway template.
4. Add **api**: *New > GitHub Repo* (this repository), branch `staging/mcp-keys`, root directory `apps/api`,
   and under Settings point the config file at `apps/api/railway.staging.json`.
   Do **not** deploy yet; set variables first (step 3).

## Step 2: database roles (once; from WSL on your machine)

The bootstrap needs a superuser connection and `psql`. Use the TCP proxy host and port that Railway shows for
the `postgres` service. In a WSL terminal (`wsl -d Ubuntu`):

```bash
export SOURCEMIND_STAGING_OWNER_PASSWORD=$(openssl rand -hex 24)
export SOURCEMIND_STAGING_RUNTIME_PASSWORD=$(openssl rand -hex 24)
export PGPASSWORD='<the postgres service POSTGRES_PASSWORD>'
/usr/lib/postgresql/18/bin/psql -h <proxy-host> -p <proxy-port> -U postgres -d railway \
  -v ON_ERROR_STOP=1 -f /mnt/d/SourceMind-Claude-STG/infra/postgres/staging-roles.sql
```

The last lines must show both roles with `rolsuper = f` and `rolbypassrls = f`. **Keep the two generated
passwords**: you type them into Railway in step 3 (`echo` them only in your own terminal, nowhere else).
The script is re-runnable. When done, **turn the TCP proxy off** again.

## Step 3: `api` variables (Railway > api > Variables; names only here)

| Variable | Value |
|---|---|
| `ENVIRONMENT` | `staging` |
| `DEBUG` | `false` |
| `AUTH_DEV_BYPASS_ENABLED` | `false` |
| `DATABASE_URL` | `postgresql+asyncpg://sourcemind_runtime:<runtime password>@${{postgres.RAILWAY_PRIVATE_DOMAIN}}:5432/sourcemind_staging` |
| `MIGRATION_DATABASE_URL` | `postgresql+asyncpg://sourcemind_owner:<owner password>@${{postgres.RAILWAY_PRIVATE_DOMAIN}}:5432/sourcemind_staging` |
| `REDIS_URL` | `${{redis.REDIS_URL}}` |
| `CLERK_SECRET_KEY`, `CLERK_PUBLISHABLE_KEY` | from the **same Clerk dev instance** the local app uses (Clerk dashboard > API keys) |
| `CLERK_AUTHORIZED_PARTIES` | `["http://localhost:3000"]` (a JSON array, not a comma list) |
| `CORS_ORIGINS` | `["http://localhost:3000"]` (a JSON array) |
| `RATE_LIMIT_FAIL_CLOSED` | `true` |
| `FF_NEO4J_ATTRIBUTION`, `FF_KAFKA_EVENTS` | `false` |
| `URL_INGESTION_ENABLED`, `SLACK_MEMORY_COMMANDS_ENABLED`, `CONNECTOR_SCHEDULER_ENABLED` | `false` |
| `MCP_ENABLED` | `true` |
| `MCP_AUTH_MODE` | `api_key` (the default is `oauth`, which Clerk cannot satisfy) |
| `MCP_RESOURCE_URL` | `https://<api public domain>/mcp` (set after step 5) |

`SOURCEMIND_RUNTIME_ROLE` is set **only inside the pre-deploy command** (step 4), not as a service variable.
Replace `postgres` / `redis` in the reference variables with the exact service names you used.

## Step 4: migrations run as a pre-deploy command (inside Railway's private network)

The app requires TLS for any database host except `*.railway.internal`, and the pgvector image has no TLS, so
migrations cannot run from your laptop through the public proxy. In **api > Settings > Deploy > Pre-deploy
command** enter:

```
sh -c 'DATABASE_URL="$MIGRATION_DATABASE_URL" SOURCEMIND_RUNTIME_ROLE=sourcemind_runtime alembic upgrade head && if [ -n "$SEED_CLERK_ID" ]; then python scripts/seed_staging.py --clerk-id "$SEED_CLERK_ID" --workspace-slug "$SEED_WORKSPACE_SLUG"; fi'
```

It runs `alembic upgrade head` as the owner (idempotent) before every deploy, then the optional seeder when
`SEED_CLERK_ID` and `SEED_WORKSPACE_SLUG` are set (step 6). **Do not copy this command to a production
service**: production migrations stay an owner-run, reviewed step (see `ARCHITECTURE.md` section 13).

## Step 5: deploy and public domain

1. Deploy `api`. Watch the build log (the image is built from `Dockerfile.staging`, which only differs from
   `Dockerfile` by having no cache mounts and by copying the seeder; regenerate it with
   `python scripts/make_staging_dockerfile.py` after any `Dockerfile` change, check with `--check`).
2. Pre-deploy log must end with `Running upgrade ... -> 20261016_0014` (or "already at head"), and the
   healthcheck `/health` must pass.
3. *Settings > Networking > Generate Domain*, then set `MCP_RESOURCE_URL` to `https://<domain>/mcp` and redeploy.

## Step 6: smoke test, workspace, seed

From any terminal (no key needed):

```
curl -s -o /dev/null -w "%{http_code}\n" https://<domain>/health                    # 200
curl -s -i -X POST https://<domain>/mcp -H "content-type: application/json" -d "{}"  # 401, www-authenticate: Bearer realm="sourcemind-mcp", error="invalid_token"
curl -s -o /dev/null -w "%{http_code}\n" https://<domain>/.well-known/oauth-protected-resource/mcp   # 404
```

The 401 line proves the Railway edge passes the `Authorization` machinery through to the app. Then, on your
machine, run the web app against staging **without** a pinned workspace:

```
cd apps/web   # .env (git-ignored): REACT_APP_USE_MOCKS=false, REACT_APP_BACKEND_URL=https://<domain>,
              # REACT_APP_CLERK_PUBLISHABLE_KEY=<same dev instance key>; REACT_APP_DEFAULT_WORKSPACE_ID unset
npm start     # http://localhost:3000/Source-Mind/
```

Sign in, **create a workspace** in the UI (note its slug), find your Clerk user id (`user_...`, Clerk
dashboard > Users), set `SEED_CLERK_ID` and `SEED_WORKSPACE_SLUG` on `api`, and **redeploy**: the pre-deploy
step seeds five fake memories (idempotent). Remove both variables afterwards if you like.

## Step 7: real-client tests over HTTPS

In Settings > API Keys create a key, then (key only in your own terminal):

```
claude mcp add --transport http sm-staging https://<domain>/mcp --header "Authorization: Bearer <key>"
$env:SM_MCP_KEY = "<key>"; codex mcp add sm-staging --url https://<domain>/mcp --bearer-token-env-var SM_MCP_KEY
```

Ask each client to search memories for "redis" in keyword mode; then revoke the key in the UI and ask again:
the next call must fail. Record results under `evidence/` with no keys.

## Teardown (production is unaffected either way)

Remove the client configs (`claude mcp remove sm-staging`, `codex mcp remove sm-staging`), revoke the keys,
then **delete the whole `staging` environment** in Railway (this removes its services and database).

## Rollback of a bad staging deploy

Redeploy the previous successful deployment of `api`, or delete and recreate the environment. Because
staging holds only fake data, the simplest recovery is to delete the environment and repeat the steps.

## Lessons from the first hosted run (2026-10-19)

* **Railway no longer loads config-as-code for new services.** Set the root directory, the Dockerfile path, the
  health check path (`/health`) and the pre-deploy command in the dashboard (`railway.staging.json` is kept only as
  a record of the values).
* **Variable-only changes did not start a deployment** here; use the deployment menu, **Redeploy**, after adding the
  `SEED_*` variables (the pre-deploy step then runs the seeder).
* **Clerk no longer shows full existing secret keys.** Copying the masked preview gives a truncated key and every
  authenticated request is a 401 ("Unable to verify the Clerk user profile", caused by Clerk's own 401). Use the value
  already in your local `apps/api/.env`, or create a new key, and paste it with no quotes or spaces.
* **Sign-in against a brand-new database** shows "create a workspace"; create `staging-test` in the app, then seed.
* **Keyword search needs no provider key** only after D-051 (commit `2b1c845`); before it, keyword mode called the
  embedding provider and failed with `internal_error`.
* **Exception logs carried local variables** (including a Redis password) before D-051; staging logs written earlier
  contain it. Treat that Redis password as exposed.
* **Measured over HTTPS through Railway's edge:** real Claude Code connected with a workspace key, search returned the
  5 seeded memories; after revoking the key in the UI the next `/mcp` request was 401 (revoke 22:43:11.969 UTC,
  next request 22:43:40.026 UTC, no request accepted in between; the 28 s is human delay, not a timing measurement).
* **Not tested on staging:** Codex over HTTPS, the remote measurement script (latency, outage), expiry, several replicas.
