# Graph projection: production rollout runbook

Status: written 2026-10-30 for D-063. Nothing here has been executed in production. Read it with
[GRAPH_PROJECTION.md](GRAPH_PROJECTION.md) (design) and
[SECURITY_FOUNDATION_ROLLOUT.md](SECURITY_FOUNDATION_ROLLOUT.md) (the conventions this follows).

## What is being rolled out

Production is the Railway project `welcoming-motivation`, environment `production`: `Source-Mind` (API),
`Source-Mind-Worker`, `Postgres`, `Redis`. Both application services build from `main` with root `/apps/api` and are
pinned to a commit with autodeploy **off** (D-052). This rollout adds four services and two flags:

| New | What | Image / command |
| --- | --- | --- |
| `neo4j` | the graph | `neo4j:5.26.24-community`, volume on `/data`, private network only |
| `redpanda` | the event transport | `redpandadata/redpanda:v24.3.14`, single node, volume, private network only |
| `graph-relay` | outbox to Redpanda | `apps/api/railway.relay.json` (`python -m sourcemind.events.relay`) |
| `graph-projector` | Redpanda to Neo4j | `apps/api/railway.projector.json` (`python -m sourcemind.events.projector`) |

Flags: `FF_KAFKA_EVENTS` and `FF_NEO4J_ATTRIBUTION`, always set together (the boot guard in `core/config.py` refuses one
without the other in production, a non-default `NEO4J_AUTH`, and any localhost URI).

**This is a release, not just a flag.** The running revision is `468d7b8`. Shipping the graph ships everything merged
since: four migrations (`20261029_0015`, `20261029_0016` and, from the team-setup work, `20261031_0017` workspace invitations and `20261031_0018` the audit trail), the dependency swap PyMuPDF to pypdfium2 and
python-Levenshtein to rapidfuzz (D-054), the co-authored-commit fix (D-055), and the graph code itself with the flags
off. Review the commit list for the pinned SHA before you start and smoke-test PDF ingestion and connector sync (step 5).

Why the order is safe: with both flags on, a workspace is answered from PostgreSQL until **all** of these hold
(`_graph_fresh`): it has a published cursor, the projector has applied up to it, it has no dead-letter gap, and **a
completed backfill has been applied to it** (its baseline). The last condition matters because each event carries one
memory: a workspace that already had data when the pipeline was switched on would look caught up after its first new
event while missing everything older. Turning the flags on before any backfill therefore changes nothing a user can see;
the graph takes over workspace by workspace as each is backfilled.

**Workspaces created after enablement** are not baselined either (nothing backfills them), so they stay on PostgreSQL until
someone runs the backfill for them. `status` lists them as `not baselined: run the backfill`. Automating that (a backfill
when a workspace is created, or a periodic job) is a follow-up; until then include it in the periodic check in step 8.

## Rollback (valid at every step)

1. Set `FF_KAFKA_EVENTS=false` and `FF_NEO4J_ATTRIBUTION=false` on the API and worker and redeploy. Reads are PostgreSQL
   from the next request; no events are written.
2. Stop `graph-relay` and `graph-projector`. Nothing is lost: unpublished outbox rows wait in PostgreSQL.
3. Neo4j and Redpanda data are disposable: the graph is rebuilt from PostgreSQL by the backfill.
4. If the release itself misbehaves, re-pin the API and worker to `468d7b8`. The two migrations are additive (new tables
   only), so the old code runs against the new schema.
5. Downgrading the migrations is a separate, deliberate act. `0015` refuses while the outbox holds rows.

## Steps

Each step names who acts, what to check before moving on, and what undoes it.

### 0. Agree the release and the window
- Pick one `main` commit SHA and record it here. Read `git log 468d7b8..<sha>`.
- Record the current state read-only: deployed commit, start commands, variable **names** (never values), regions.
- Keep autodeploy off. Independent services cannot enforce migration order.

### 1. Back up PostgreSQL
Take a fresh encrypted logical backup (and a Railway volume snapshot) as in the D-021 rollout; confirm it restores
somewhere disposable. Do not continue without one: step 4 changes the production schema.

### 2. Provision the storage tier (flags still off, nothing connected yet)
Create both as **image services** in the same environment, region `us-west2` (next to the API; the database is in `sfo`),
with **no public domain and no TCP proxy**. Neo4j Community has no database-enforced tenant isolation; its only
protection is that nothing but the API, worker and projector can reach it.

`neo4j`
- image `neo4j@sha256:f66304b9511c60d33555a2c451f88e03d82d1ebc893f32d84c98a6b326096435` (tag `5.26.24-community`), volume
  mounted at `/data`, ~2 GB memory
- `NEO4J_AUTH=neo4j/${{shared.NEO4J_PASSWORD}}`. The password is a **shared variable the owner creates in the Railway
  dashboard** (random, 32+ characters, sealed if offered). Railway hides variable values from tooling, so nobody but the
  owner ever sees it and it never passes through a terminal or chat. Other services reference it the same way.
- memory sized to the plan: `NEO4J_server_memory_heap_initial__size`, `NEO4J_server_memory_heap_max__size`,
  `NEO4J_server_memory_pagecache_size` (the local default was heap 512m-1G). Confirm the setting names in the first boot log.
- no APOC (unrestricted `apoc.*` is a file-read and SSRF primitive and nothing here uses it)

`redpanda`
- image `redpandadata/redpanda@sha256:3b68cc8a7fca4f03c6aa65dc6bd8d86a10644b282fcbad43edd12636ff2ef6fc` (tag `v24.3.14`),
  volume mounted at `/var/lib/redpanda/data`, **memory limit 2 GB** (see below), start command:

```
redpanda start --smp=1 --memory=1G --reserve-memory=0M --overprovisioned --node-id=0 --check=false
  --set redpanda.developer_mode=false --set redpanda.write_caching_default=false
  --kafka-addr=internal://0.0.0.0:9092 --advertise-kafka-addr=internal://redpanda.railway.internal:9092
  --rpc-addr=0.0.0.0:33145 --advertise-rpc-addr=redpanda.railway.internal:33145
```

- **Why these flags (checked locally with the pinned image, 2026-10-30).** The image's shipped config has
  `developer_mode: true` and write caching on, i.e. relaxed fsync, even without `--mode`; `--mode` accepts only
  `dev-container`. A published event lost by the broker would not be visible (the cursor has already moved past it), so
  production overrides both with `--set`. With `developer_mode` off Redpanda refuses to start below 1 GiB per core
  (`Memory below recommended`), hence `--memory=1G` and a 2 GB container. Verified: it starts healthy, serves a message,
  keeps it across a restart, and the whole real-service pipeline suite (33 tests: projection, end to end, parity, reads)
  passes against this exact configuration with both images pinned by digest.
- Any Redpanda data loss still means "backfill every workspace".

Verify: both deployments healthy; from the API service, a TCP connect to `neo4j.railway.internal:7687` and
`redpanda.railway.internal:9092` succeeds; neither has a public address.
Undo: delete the two services and their volumes.

**Provisioned so far (2026-10-31, production project `welcoming-motivation`, flags off, nothing running against it):**
`neo4j` and `redpanda` are running (private network only); `graph-relay` and `graph-projector` exist with their settings and
variables but **no source attached, so they are not running**; `NEO4J_URI`, `NEO4J_AUTH` and `KAFKA_BOOTSTRAP_SERVERS` are set
on the API and worker without a redeploy. Attach the relay and projector sources only after steps 3, 4 and 5.

### 3. Create the relay database role (the owner runs this)
Postgres has no public address (no TCP proxy), so do this from inside Railway's network; do not open one. The password is
a shared variable the owner creates, so it never passes through tooling:

1. Dashboard, project `welcoming-motivation`, Settings, Shared Variables, `production`: add `GRAPH_RELAY_PASSWORD` (random,
   32+ letters and digits) and apply it. `graph-relay` already references it in `GRAPH_RELAY_DATABASE_URL`.
2. Open a shell in the Postgres container and run the SQL (the `\password` meta-command prompts for the value and hashes it
   locally, so it does not land in shell history; type the same value as the shared variable):

```
railway ssh -p 06c35f15-133d-41d3-95c6-6e33ed1927c2 -e production -s Postgres
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

```sql
CREATE ROLE sourcemind_graph_relay LOGIN
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
\password sourcemind_graph_relay
ALTER ROLE sourcemind_graph_relay SET timezone = 'UTC';
GRANT CONNECT ON DATABASE <the database name> TO sourcemind_graph_relay;
GRANT USAGE ON SCHEMA public TO sourcemind_graph_relay;
```


The role gets no policy bypass. The migration in step 4 grants it `SELECT` and `UPDATE (published_at)` on `graph_outbox`
and `SELECT` plus `INSERT`/`UPDATE (published_seq, updated_at)` on `graph_publication_cursor`, and nothing else.
Undo: `DROP ROLE sourcemind_graph_relay` (after step 4 is undone).

### 4. Migrate (as the table-owner role, never as the runtime role)
`alembic/env.py` takes its connection from `DATABASE_URL`, so the owner URL goes in that variable for this one process only.
Do not put the owner credential on the API or worker. Two ways, both keep it inside Railway's network:

- **Recommended: a temporary one-shot service `graph-migrate`**, built from the pinned `main` SHA (root `/apps/api`,
  Dockerfile `Dockerfile.worker`), with `DATABASE_URL=${{Postgres.DATABASE_URL}}` (a reference, so nobody reads it) or the
  dedicated owner role's URL, the two role variables below, restart policy NEVER, and the start command
  `sh -c 'alembic current && alembic upgrade head && alembic current'`. It runs once; read its deploy log for the two
  `alembic current` lines and the new head; then delete the service. It also needs the settings-validation variables
  (copied from the worker by reference, as `graph-relay` has).
- **Or by hand** from a shell inside a service container (`railway ssh -p ... -e production -s <service>`) with
  `DATABASE_URL` set to the owner URL for the single command.

With the owner connection string and these two variables set:

```
SOURCEMIND_RUNTIME_ROLE=<the production runtime role>
SOURCEMIND_GRAPH_RELAY_ROLE=sourcemind_graph_relay
alembic current            # expect 20261016_0014 (confirm before continuing)
alembic upgrade head        # applies 20261029_0015, 20261029_0016, 20261031_0017 and 20261031_0018
```

Verify the grants exactly (nothing wider than listed):

```sql
SELECT grantee, privilege_type FROM information_schema.role_table_grants
WHERE table_name IN ('graph_outbox', 'graph_publication_cursor') ORDER BY 1, 2;
SELECT grantee, privilege_type, column_name FROM information_schema.column_privileges
WHERE table_name IN ('graph_outbox', 'graph_publication_cursor')
  AND grantee = 'sourcemind_graph_relay' AND privilege_type = 'UPDATE';
```

Expected: the runtime role has `INSERT` on `graph_outbox` and `SELECT` on `graph_publication_cursor`, nothing more; the relay
role has `SELECT` on both, `UPDATE (published_at)` on the outbox, `INSERT` on the cursor and `UPDATE (published_seq, updated_at)`.
Undo: the downgrade (separate, deliberate; see Rollback).

### 5. Ship the release with the flags off
1. Re-pin the API and worker to the agreed SHA and redeploy, worker first, then API, as in the security rollout.
2. Verify `/health`, a normal authenticated request, and the authorization matrix are unchanged.
3. Exercise what the dependency swap touched: ingest a PDF, run one connector sync.
4. Create `graph-relay` and `graph-projector` from the same SHA using `railway.relay.json` / `railway.projector.json`.
   Give each the worker's production variable set (they load the same settings, so the production validators apply to
   them too) plus:
   - `NEO4J_URI=bolt://neo4j.railway.internal:7687`, `NEO4J_AUTH=${{neo4j.NEO4J_AUTH}}`
   - `KAFKA_BOOTSTRAP_SERVERS=redpanda.railway.internal:9092`
   - relay only: `GRAPH_RELAY_DATABASE_URL` (the relay role; a secret)
5. Verify from their logs: `graph.topic_created` (or already existing), `graph.relay_started`, `graph.projector_started`,
   `neo4j.connected`, then `graph.relay_lag pending=0`. With the flags still off they idle.
Undo: re-pin to `468d7b8`; delete the two new services.

### 6. Enable
Set `FF_KAFKA_EVENTS=true` and `FF_NEO4J_ATTRIBUTION=true` together, plus `NEO4J_URI`, `NEO4J_AUTH` and
`KAFKA_BOOTSTRAP_SERVERS` on the API and the worker (the worker's ingestion path emits events too), and redeploy both.
Behaviour is unchanged for users until a workspace is backfilled. New writes now appear in `graph_outbox`.
Undo: Rollback step 1.

### 7. Backfill, one workspace at a time, smallest first
```
python -m sourcemind.events.backfill --workspace-id <uuid> [--as-user <uuid of an active member>]
```
It reads PostgreSQL through normal row-level security as an active owner, so it needs no bypass role. After each:

```
python -m sourcemind.events.status --workspace-id <uuid>
```
Proceed only when the state is `trusted` (applied has reached published, a baseline from the completed backfill, no gap,
nothing pending; exit code 0). Then call the four
endpoints for that workspace and confirm `source: "graph"`, and that lineage, one-hop related and the collaboration graph
equal their PostgreSQL answers (the parity the tests assert).
A state of `gap: run the backfill` means an event was dead-lettered; `not baselined: run the backfill` means no completed
backfill has been applied yet. Either way, run the backfill again for that workspace. Nothing is wrong in the meantime:
that workspace is simply answered from PostgreSQL.

### 8. Observe for 24 hours
Watch `graph.relay_lag` (pending and oldest seconds), the projector batch lines and any `graph.dead_lettered`, Neo4j memory,
the share of graph versus PostgreSQL answers, and endpoint latency (the graph read ceiling is `NEO4J_QUERY_TIMEOUT_SECONDS`,
default 2 s; on timeout the API falls back and the client sees nothing). `python -m sourcemind.events.status` with no
arguments checks **every** workspace and exits non-zero if any is not trusted (a `--limit` below the total is a partial
check and also exits non-zero), so it works as a periodic check; with `--workspace-id` an id nothing is known about is
reported as `not in the graph yet`, never as success.

## Operating notes (observed in the local live run, 2026-10-30)

- **Neo4j down:** the API answers from PostgreSQL at once; depth is reported honestly (`depth_served` 1 of 3) and owners are
  direct contributors only. No client-visible error.
- **Neo4j rebuilt empty** (volume lost): reads stay on PostgreSQL (no watermark, so not fresh). Restart the projector (it
  creates the schema at start) and backfill each workspace.
- **A poison event** is dead-lettered and its workspace marked gapped; a later good event does not hide it. Backfill clears it.
  While Neo4j is unreachable the projector retries instead of dead-lettering, so an outage alone never creates a gap.
- **One relay at a time** (a PostgreSQL advisory lock). A second instance exits with code 3 and Railway restarts it, so a
  rolling deploy briefly shows `graph.relay_already_running`. That is expected.
- **Redpanda data loss** means published events vanished: backfill every workspace.
- Licences: Neo4j Community is GPLv3 and Redpanda is BSL 1.1. Running the unmodified images as separate services is the
  intended use; confirm that fits your obligations (this document is not legal advice).

## Not verified here

Railway-specific behaviour (private DNS names, variable references, volume sizing, restart behaviour), PostgreSQL 16 (the
tests ran on 18, which production uses), load, multi-partition topics, and anything about the Redpanda flags beyond the local run above.
