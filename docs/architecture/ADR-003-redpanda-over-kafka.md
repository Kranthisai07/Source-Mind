# ADR-003: Redpanda as Local Dev Kafka Replacement

**Status:** Accepted
**Date:** 2025-03-11
**Deciders:** Principal Architect

---

## Context

The production architecture uses Apache Kafka for event sourcing (memory.created, attribution.computed, etc.). Running a full Kafka cluster locally (Kafka + ZooKeeper + Schema Registry) requires 3+ containers and consumes significant memory on developer machines.

## Decision

Use **Redpanda** in local development (docker-compose) and CI. Use **Amazon MSK (Apache Kafka)** in staging and production.

The application uses the standard Kafka protocol (`aiokafka` client) which is fully compatible with Redpanda's Kafka API. No application code changes are required between environments.

## Rationale

| Concern | Kafka (local) | Redpanda |
|---------|---------------|----------|
| Container count | 3 (Kafka + ZK + Schema Registry) | 1 |
| Memory footprint | ~2GB | ~256MB |
| Cold start time | ~30s | ~3s |
| Kafka API compatibility | Native | 100% compatible |
| Production parity | Exact | Protocol-compatible |

**What we give up:**
- ZooKeeper ensemble coordination (not needed for single-broker dev)
- Exact JVM behavior matching (rarely relevant for application-level testing)

## Consequences

- Developer machines can run the full stack in Docker with 8GB RAM
- CI runs all integration tests in under 2 minutes
- Any Kafka-specific behavior (e.g. log compaction edge cases) must be tested against MSK in staging before production deployment
- Redpanda Console (included in docker-compose) provides a UI for monitoring topics during development
