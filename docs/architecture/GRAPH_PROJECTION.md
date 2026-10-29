# Graph projection (Neo4j + Redpanda)

Neo4j holds a **read-only, rebuildable projection** of PostgreSQL. PostgreSQL stays the source of truth; nothing is
ever written to the graph first, and every graph read has a PostgreSQL fallback. This document is the design and, as
the pipeline lands, the runbook. It supersedes the graph half of the deleted ADR-001 (`git show
0044a53^:docs/architecture/ADR-001-database-strategy.md`); the reasoning is in `DECISIONS.md` (D-058).

## Status

| Piece | State |
|---|---|
| Settings, production safety checks, `.env.example` | **Done** (this stage) |
| Neo4j driver with a workspace-scoped query guard (`core/graph.py`) | **Done** |
| Event envelope (`events/envelope.py`) and the relay's producer (`events/producer.py`) | **Done** |
| `graph_outbox` table, grants and migration `20261029_0015` | **Done** (unit-tested with a mocked `op`, and run for real on PostgreSQL 18: upgrade, grants, downgrade guard, re-upgrade; not run on PostgreSQL 16) |
| Compose: graph services behind `--profile graph`, pinned images, loopback ports, no APOC | **Done** |
| Outbox rows written from the code paths that change memories (`services/graph/outbox.py`) | **Done**, flag-gated (`FF_KAFKA_EVENTS`). Verified on real PostgreSQL for edits, handoff assignment, conflict resolution (kept, merged, deferred) and source retirement; the ingestion worker and delete route are wired but not driven by a real-database test |
| Relay (`python -m sourcemind.events.relay`) | **Done**; verified on real PostgreSQL and through a real Redpanda |
| Projector (`python -m sourcemind.events.projector`) and the Cypher mapper | **Done**; verified against a real Neo4j 5.26.24 and Redpanda v24.3.14, including out-of-order delivery, duplicates, wholesale replacement, the dead-letter topic, and one end-to-end run from the PostgreSQL outbox to the graph |
| Backfill (`python -m sourcemind.events.backfill`) | **Done**; verified on real PostgreSQL |
| Freshness cursor (`graph_publication_cursor`, migration `20261029_0016`) | **Done**; the relay advances it in the transaction that marks rows published; verified on real PostgreSQL |
| Graph-backed read endpoints (`api/v1/graph.py`, `services/graph/reads.py`): lineage, related, owners, collaboration graph | **Done**; each answers from Neo4j when enabled, fresh and agreeing with PostgreSQL, otherwise from PostgreSQL; parity-tested against real Neo4j, Redpanda and PostgreSQL |
| Web views | Planned (next stage) |

Verified means: run against disposable real services on a developer machine (Docker Desktop, Windows host with PostgreSQL in WSL) and defined in `.github/workflows/graph-ci.yml`, which has not yet run on GitHub. Not verified: the Neo4j 6.x Python driver against anything but 5.26, Redpanda or Neo4j under load, a production deployment of either, and PostgreSQL 16 (the tests used 18). Do not enable the flags in production.

## Why an outbox, not publishing from the request

* Almost all memories and attributions are created by Celery workers. Each task runs under its own `asyncio.run`, so
  a long-lived async Kafka producer cannot live there, and the old producer silently dropped events when it was not
  started.
* Publishing inline after the commit loses the event on a crash between commit and send.

So a change writes one row to `graph_outbox` **in the same transaction** as the domain rows. A separate relay process
publishes rows to Redpanda in `seq` order; a separate projector consumes them into Neo4j.

```
request / Celery transaction --(same transaction)--> graph_outbox
graph relay      --> Redpanda topic sourcemind.graph.events (key = workspace_id)
graph projector  --> Neo4j   (idempotent MERGE, guarded by seq; offsets committed after apply; DLQ)
API reads        --> Neo4j if enabled and fresh, otherwise the existing PostgreSQL query
```

## Events

One event is the **full current state of one memory**, not a delta, so replaying, duplicating or reordering events
cannot corrupt the graph: the projector applies an event only when its `seq` is newer than the node's. Envelope fields:
`schema_version, event_id, seq, occurred_at, workspace_id, type, aggregate_id, payload`. Payloads carry ids, versions
and weights only, never memory content, embeddings, emails or display names (Kafka retains what it is given).

## Reads

Four endpoints, all under `/v1`, all enveloped, all rate-limited with the analytics budget, all authorised in PostgreSQL first
(the authorised workspace id is the only thing that scopes a graph query): `GET /memories/{id}/lineage`,
`GET /memories/{id}/related?depth=1..3&types=&limit=`, `GET /memories/{id}/owners` and
`GET /workspaces/{id}/analytics/collaboration-graph`. Every body says which backend answered (`source`).

The graph may answer only when `FF_NEO4J_ATTRIBUTION` is on and a driver exists, **and** the projector's applied `seq`
for the workspace has reached the relay's published `seq` (the cursor), **and** (memory-scoped reads) the graph's node for
the memory agrees with PostgreSQL about version, currency and deletion, **and** the answer has no placeholder nodes.
Anything else, including any graph exception, is answered from PostgreSQL inside a savepoint; the client never sees a
graph failure. Names, previews and timestamps are read from PostgreSQL under the caller's row-level security, never stored
in the graph.

What differs by design: PostgreSQL serves one hop for `related` (and says so in `depth_served`) and only direct
contributors for `owners`; the graph follows up to three hops and ranks contributors reached through relations
(`score = sum(weight * 0.5^hops)`). Where both can answer (lineage, one-hop related, collaboration, owners of a memory with
no relations) the answers are equal, and the parity tests keep them so.

Known limit: a change is not visible to the graph until the relay publishes it (settle window plus poll, a few seconds), and
the cursor cannot see events still in that window. Memory-scoped reads cover the case that matters, opening a memory you
just edited, with the node-agreement check; the workspace-wide collaboration graph is simply eventually consistent.

## Graph model

Neo4j Community has no node keys and no row-level security, so:

* every node and relationship carries `workspace_id`, and keys are synthetic (`"<workspace_id>:<id>"`) under a
  single-property uniqueness constraint (`core/graph.py:SCHEMA_STATEMENTS`);
* every query goes through `run_read` / `run_write`, which refuse a query without `$workspace_id` and bind it from the
  authenticated context, never from a request parameter. Cypher is parametrised and never built from user input.

## Running the real-service tests

```
export GRAPH_TEST_NEO4J_PASSWORD=<any local value>
docker compose -f infra/docker-compose.graph-test.yml up -d --wait     # Neo4j :57687, Redpanda :59092 (loopback)
# plus the disposable PostgreSQL of docker-compose.security-test.yml with migrations applied,
# MIGRATION_DATABASE_URL and TEST_GRAPH_RELAY_DATABASE_URL set, then, from apps/api:
python -m pytest -o addopts='' tests/integration/test_graph_projection_real_services.py   tests/integration/test_graph_outbox_real_db.py tests/integration/test_graph_pipeline_end_to_end.py
```

The Neo4j tests wipe the database they connect to and refuse anything but the fixed loopback ports. A
development-sized Redpanda (512 MB) allows only about 128 partitions in total and silently stops creating topics beyond
that, so the tests use single-partition topics and delete them afterwards; if you leave topics behind, the relay and
projector now fail at start with `TopicsNotReadyError` rather than hanging. CI does this with
random per-run credentials and no repository secrets.

## Local development

```
docker compose -f infra/docker-compose.yml --profile graph up -d
```

The graph services bind to `127.0.0.1` only. `local-roles.sql` creates the `sourcemind_graph_relay` role on a **new**
volume; to use it on an existing volume recreate it (`down -v` deletes local data) and set
`SOURCEMIND_GRAPH_RELAY_ROLE=sourcemind_graph_relay` before `alembic upgrade head` so the migration grants it
`SELECT` and `UPDATE (published_at)` on `graph_outbox` and nothing else. The runtime role may only `INSERT`.

## Production rules enforced at boot (`core/config.py`)

With either graph flag on in production: both flags must be on together, `NEO4J_AUTH` must not be the development
default, and neither `NEO4J_URI` nor `KAFKA_BOOTSTRAP_SERVERS` may point at localhost. Production hosting of Neo4j and a
broker is **not** decided or provisioned; the flags stay off.

## Known limits

* Neo4j Community has no roles: isolation is application-enforced (above), not database-enforced.
* Erasure: the graph is rebuildable from PostgreSQL, which is the only dependable answer; no hard user-erasure feature
  exists in PostgreSQL either.
* `graph_outbox.seq` is assigned at insert, not commit. The relay will leave rows younger than a settle window for the
  next poll; this is a documented shortcut, to be upgraded if reconciliation finds drift.
* Neo4j Community is GPLv3 and Redpanda is BSL; they run as separate processes in development and CI only.
