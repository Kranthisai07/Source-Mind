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
| Outbox rows written from the code paths that change memories (`services/graph/outbox.py`) | **Done**, flag-gated (`FF_KAFKA_EVENTS`). Verified on real PostgreSQL for edits and handoff assignment; ingestion, conflict resolution, source retirement and delete are wired but not exercised by any real-database test yet |
| Relay (`python -m sourcemind.events.relay`) | **Done**; ordering, settle window, advisory lock and grants verified on real PostgreSQL with a fake publisher; **not run against a broker** |
| Projector (`python -m sourcemind.events.projector`) and the Cypher mapper | **Done** and unit-tested against a recording runner; **not run against Neo4j or a broker** |
| Backfill (`python -m sourcemind.events.backfill`) | **Done**; verified on real PostgreSQL |
| Freshness watermark for reads, graph-backed read endpoints, web views | Planned (later stages) |

The Cypher in `events/graph_mapper.py` has never been executed against a Neo4j server, and nothing has run against Redpanda or Kafka. Do not enable the flags in production.

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

## Graph model

Neo4j Community has no node keys and no row-level security, so:

* every node and relationship carries `workspace_id`, and keys are synthetic (`"<workspace_id>:<id>"`) under a
  single-property uniqueness constraint (`core/graph.py:SCHEMA_STATEMENTS`);
* every query goes through `run_read` / `run_write`, which refuse a query without `$workspace_id` and bind it from the
  authenticated context, never from a request parameter. Cypher is parametrised and never built from user input.

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
