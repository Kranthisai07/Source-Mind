# SourceMind Evaluation Report

Generated: 2026-08-22T16:41:10.665412+00:00  
Ground-truth items: 5

## Summary

| Retriever | Knowledge Retention (recall@5) | Attribution Accuracy | Role Scope (engineer) | Role Scope (manager) | Latency p95 (ms) |
|-----------|--------------------------------|----------------------|-----------------------|----------------------|------------------|
| naive_rag | 1.000 | 0.600 | 1.000 | 0.000 | 236.270 |
| sourcemind | 0.600 | 0.000 | 1.000 | 0.000 | 653.470 |

## Excluded Metrics

**conflict_detection** — excluded by design, not missing.

> conflict_detection: excluded from this run — dataset lacks labeled negative pairs; system now includes a novel human-gated severity-tiered resolution design not present in comparable systems (Supermemory, Mem0 both auto-resolve via LLM with no human oversight — see paper Section X). Full evaluation of this feature is planned as follow-up work.

## Detailed Results

### naive_rag

**knowledge_retention**

| Field | Value |
|-------|-------|
| metric | knowledge_retention |
| score | 1.000 |
| hits | 5 |
| total | 5 |
| top_k | 5 |

**attribution_accuracy**

| Field | Value |
|-------|-------|
| metric | attribution_accuracy |
| score | 0.600 |
| correct | 3 |
| total | 5 |

**role_scoped_retrieval_engineer**

| Field | Value |
|-------|-------|
| metric | role_scoped_retrieval |
| role | engineer |
| score | 1.000 |
| total_queries | 5 |
| in_scope_results | 25 |
| total_results | 25 |

**role_scoped_retrieval_manager**

| Field | Value |
|-------|-------|
| metric | role_scoped_retrieval |
| role | manager |
| score | 0.000 |
| total_queries | 0 |
| in_scope_results | 0 |
| total_results | 0 |

**latency**

| Field | Value |
|-------|-------|
| metric | latency |
| p50_ms | 184.530 |
| p95_ms | 236.270 |
| p99_ms | 236.270 |
| mean_ms | 185.750 |
| min_ms | 137.540 |
| max_ms | 236.270 |
| total_queries | 5 |

### sourcemind

**knowledge_retention**

| Field | Value |
|-------|-------|
| metric | knowledge_retention |
| score | 0.600 |
| hits | 3 |
| total | 5 |
| top_k | 5 |

**attribution_accuracy**

| Field | Value |
|-------|-------|
| metric | attribution_accuracy |
| score | 0.000 |
| correct | 0 |
| total | 5 |

**role_scoped_retrieval_engineer**

| Field | Value |
|-------|-------|
| metric | role_scoped_retrieval |
| role | engineer |
| score | 1.000 |
| total_queries | 5 |
| in_scope_results | 25 |
| total_results | 25 |

**role_scoped_retrieval_manager**

| Field | Value |
|-------|-------|
| metric | role_scoped_retrieval |
| role | manager |
| score | 0.000 |
| total_queries | 0 |
| in_scope_results | 0 |
| total_results | 0 |

**latency**

| Field | Value |
|-------|-------|
| metric | latency |
| p50_ms | 609.060 |
| p95_ms | 653.470 |
| p99_ms | 653.470 |
| mean_ms | 621.020 |
| min_ms | 603.110 |
| max_ms | 653.470 |
| total_queries | 5 |
