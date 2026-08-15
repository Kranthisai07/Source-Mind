# ADR-001: Dual-Database Strategy — PostgreSQL + pgvector + Neo4j

**Status:** Accepted
**Date:** 2025-03-11
**Deciders:** Principal Architect

---

## Context

SourceMind needs to support three distinct query patterns simultaneously:
1. **Semantic similarity search** — "Find memories semantically similar to X"
2. **Attribution chain traversal** — "Show all contributors across the evolution of this memory"
3. **Standard relational queries** — "Fetch a workspace's memories filtered by tag and date"

Each pattern has different performance characteristics and is best served by different storage systems.

## Decision

Use PostgreSQL 16 + pgvector as the **primary database** for all relational data and vector similarity search. Use Neo4j 5 as a **secondary, read-optimized** store for attribution graph traversal.

### PostgreSQL (primary)
- All relational data: organizations, workspaces, users, documents, memories
- Vector embeddings: `vector(3072)` column with HNSW index for semantic search
- Full-text search: generated `tsvector` column with GIN index for BM25
- Row-level security: workspace isolation enforced at DB level
- Source of truth for all data

### Neo4j (secondary)
- Attribution chains: `(:User)-[:CONTRIBUTED_TO {weight: 0.35}]->(:Memory)`
- Relationship graph: traversal queries not efficient in SQL
- Populated asynchronously by Kafka events — eventual consistency acceptable
- Read-only from API perspective; writes go to Postgres first

## Rationale

**Why not store vectors in a dedicated vector DB (Pinecone, Weaviate)?**
- pgvector with HNSW on 1M vectors delivers p95 < 50ms — sufficient for our targets
- Eliminates an entire service deployment (operational cost)
- Enables JOINs between vector results and relational data in a single query
- One less moving part during development and early production

**Why Neo4j for attribution rather than pure PostgreSQL?**
- Attribution chains can be 10+ hops deep (A edited B, B was derived from C, C was reviewed by D...)
- Recursive CTEs in PostgreSQL degrade beyond 5-6 hops
- Neo4j's native graph traversal is O(1) per hop regardless of graph size
- `shortestPath` and `MATCH (a)-[*1..10]->(b)` patterns have no SQL equivalent

**Why not a graph-only database for everything?**
- The relational model is too mature and well-tooled to abandon for core CRUD operations
- pgvector eliminates the need for a separate vector store
- Dual-write is acceptable given eventual consistency for attribution display

## Consequences

**Positive:**
- Optimal query patterns for each data shape
- PostgreSQL's ACID guarantees protect the source of truth
- pgvector HNSW delivers sub-50ms semantic search without external dependency
- Neo4j enables rich attribution visualization queries

**Negative:**
- Dual-write complexity: all attribution writes go to Postgres + Kafka → Neo4j
- Eventual consistency in Neo4j means attribution graphs may lag by 1-2 seconds
- Operational burden of running two database systems
- Developers must understand both SQL and Cypher

**Mitigation:**
- Neo4j writes are fire-and-forget via Kafka (never block request path)
- Clear documentation that Postgres is source of truth
- Neo4j used only for graph traversal; all other reads from Postgres
