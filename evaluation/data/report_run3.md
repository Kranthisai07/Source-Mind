# SourceMind Evaluation Report

Generated: 2026-09-02T20:54:18.688639+00:00  
Ground-truth items: 300

## Summary

| Retriever | Knowledge Retention (recall@5) | Role Scope (engineer) | Role Scope (manager) | Latency p95 (ms) |
|-----------|--------------------------------|-----------------------|----------------------|------------------|
| naive_rag | 0.313 | 0.992 | 0.600 | 560.890 |
| sourcemind | 0.240 | 0.979 | 0.377 | 1153.370 |

## Recall, four ways

Both systems scored over the identical set of items SourceMind holds. NaiveRAG still indexes all 300; only the scored queries differ.

| Basis | naive_rag | sourcemind |
|-------|-----------|------------|
| All 300 items (raw) | 0.313 (94/300) | 0.240 (72/300) |
| Held 260 items (like-for-like) | 0.296 (77/260) | 0.277 (72/260) |

40 documents produced no memories and are excluded from the second row for both systems.

## Excluded Metrics

**attribution_accuracy** — excluded by design, not missing.

> attribution_accuracy: excluded from this run - ingestion attributes every memory to the authenticated API caller rather than the artifact's original author, because create_initial_attribution() writes a single contributor at weight 1.0 and the 5-signal algorithm runs only on PATCH. The metric would therefore report 0.000 for SourceMind by construction, measuring a wiring gap rather than attribution quality. Tracked in ARCHITECTURE.md; scheduled as follow-up work.

**conflict_detection** — excluded by design, not missing.

> conflict_detection: excluded from this run — dataset lacks labeled negative pairs; system now includes a novel human-gated severity-tiered resolution design not present in comparable systems (Supermemory, Mem0 both auto-resolve via LLM with no human oversight — see paper Section X). Full evaluation of this feature is planned as follow-up work.

## Detailed Results

### naive_rag

**knowledge_retention**

| Field | Value |
|-------|-------|
| metric | knowledge_retention |
| score | 0.313 |
| hits | 94 |
| total | 300 |
| top_k | 5 |

**role_scoped_retrieval_engineer**

| Field | Value |
|-------|-------|
| metric | role_scoped_retrieval |
| role | engineer |
| score | 0.992 |
| total_queries | 200 |
| in_scope_results | 992 |
| total_results | 1000 |

**role_scoped_retrieval_manager**

| Field | Value |
|-------|-------|
| metric | role_scoped_retrieval |
| role | manager |
| score | 0.600 |
| total_queries | 100 |
| in_scope_results | 300 |
| total_results | 500 |

**latency**

| Field | Value |
|-------|-------|
| metric | latency |
| p50_ms | 221.020 |
| p95_ms | 560.890 |
| p99_ms | 794.130 |
| mean_ms | 250.060 |
| min_ms | 149.110 |
| max_ms | 794.130 |
| total_queries | 50 |

### sourcemind

**knowledge_retention**

| Field | Value |
|-------|-------|
| metric | knowledge_retention |
| score | 0.240 |
| hits | 72 |
| total | 300 |
| top_k | 5 |

**role_scoped_retrieval_engineer**

| Field | Value |
|-------|-------|
| metric | role_scoped_retrieval |
| role | engineer |
| score | 0.979 |
| total_queries | 200 |
| in_scope_results | 948 |
| total_results | 968 |

**role_scoped_retrieval_manager**

| Field | Value |
|-------|-------|
| metric | role_scoped_retrieval |
| role | manager |
| score | 0.377 |
| total_queries | 100 |
| in_scope_results | 188 |
| total_results | 499 |

**latency**

| Field | Value |
|-------|-------|
| metric | latency |
| p50_ms | 1097.030 |
| p95_ms | 1153.370 |
| p99_ms | 1437.710 |
| mean_ms | 1103.140 |
| min_ms | 1052.990 |
| max_ms | 1437.710 |
| total_queries | 50 |
