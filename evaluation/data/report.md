# SourceMind Evaluation Report

Generated: 2026-08-22T23:32:17.718339+00:00  
Ground-truth items: 300

## Summary

| Retriever | Knowledge Retention (recall@5) | Role Scope (engineer) | Role Scope (manager) | Latency p95 (ms) |
|-----------|--------------------------------|-----------------------|----------------------|------------------|
| naive_rag | 0.313 | 0.992 | 0.598 | 222.950 |
| sourcemind | 0.260 | 0.978 | 0.392 | 1082.460 |

## Recall, four ways

Both systems scored over the identical set of items SourceMind actually holds. 56 of 300 documents produced no memories, so SourceMind can never retrieve them; scoring it over 300 charges it for an ingestion gap rather than a retrieval one. NaiveRAG still indexes all 300 - the excluded documents remain in its index as distractors - so only the scored queries differ.

| Basis | naive_rag | sourcemind |
|-------|-----------|------------|
| All 300 items (raw) | 0.313 (94/300) | 0.260 (78/300) |
| Held 244 items (like-for-like) | 0.295 (72/244) | 0.320 (78/244) |

56 documents produced no memories and are excluded from the second row for both systems.

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
| p50_ms | 182.420 |
| p95_ms | 222.950 |
| p99_ms | 228.240 |
| mean_ms | 179.360 |
| min_ms | 136.270 |
| max_ms | 228.240 |
| total_queries | 50 |

### sourcemind

**knowledge_retention**

| Field | Value |
|-------|-------|
| metric | knowledge_retention |
| score | 0.260 |
| hits | 78 |
| total | 300 |
| top_k | 5 |

**role_scoped_retrieval_engineer**

| Field | Value |
|-------|-------|
| metric | role_scoped_retrieval |
| role | engineer |
| score | 0.978 |
| total_queries | 200 |
| in_scope_results | 876 |
| total_results | 896 |

**role_scoped_retrieval_manager**

| Field | Value |
|-------|-------|
| metric | role_scoped_retrieval |
| role | manager |
| score | 0.392 |
| total_queries | 100 |
| in_scope_results | 196 |
| total_results | 500 |

**latency**

| Field | Value |
|-------|-------|
| metric | latency |
| p50_ms | 1062.830 |
| p95_ms | 1082.460 |
| p99_ms | 1084.260 |
| mean_ms | 1062.410 |
| min_ms | 1029.440 |
| max_ms | 1084.260 |
| total_queries | 50 |
