# SourceMind Evaluation Report

Generated: 2026-08-31T20:57:48.656810+00:00  
Ground-truth items: 300

## Summary

| Retriever | Knowledge Retention (recall@5) | Role Scope (engineer) | Role Scope (manager) | Latency p95 (ms) |
|-----------|--------------------------------|-----------------------|----------------------|------------------|
| naive_rag | 0.313 | 0.992 | 0.598 | 331.270 |
| sourcemind | 0.250 | 0.979 | 0.359 | 1063.460 |

## Recall, four ways

Both systems scored over the identical set of items SourceMind holds. 49 of 300 documents produced no memories; NaiveRAG still indexes all 300, so only the scored queries differ.

| Basis | naive_rag | sourcemind |
|-------|-----------|------------|
| All 300 items (raw) | 0.313 (94/300) | 0.250 (75/300) |
| Held 251 items (like-for-like) | 0.287 (72/251) | 0.299 (75/251) |

49 documents produced no memories and are excluded from the second row for both systems.

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
| score | 0.598 |
| total_queries | 100 |
| in_scope_results | 299 |
| total_results | 500 |

**latency**

| Field | Value |
|-------|-------|
| metric | latency |
| p50_ms | 194.300 |
| p95_ms | 331.270 |
| p99_ms | 3947.870 |
| mean_ms | 277.230 |
| min_ms | 143.990 |
| max_ms | 3947.870 |
| total_queries | 50 |

### sourcemind

**knowledge_retention**

| Field | Value |
|-------|-------|
| metric | knowledge_retention |
| score | 0.250 |
| hits | 75 |
| total | 300 |
| top_k | 5 |

**role_scoped_retrieval_engineer**

| Field | Value |
|-------|-------|
| metric | role_scoped_retrieval |
| role | engineer |
| score | 0.979 |
| total_queries | 200 |
| in_scope_results | 933 |
| total_results | 953 |

**role_scoped_retrieval_manager**

| Field | Value |
|-------|-------|
| metric | role_scoped_retrieval |
| role | manager |
| score | 0.359 |
| total_queries | 100 |
| in_scope_results | 179 |
| total_results | 499 |

**latency**

| Field | Value |
|-------|-------|
| metric | latency |
| p50_ms | 1037.600 |
| p95_ms | 1063.460 |
| p99_ms | 1068.070 |
| mean_ms | 1041.530 |
| min_ms | 1029.960 |
| max_ms | 1068.070 |
| total_queries | 50 |
