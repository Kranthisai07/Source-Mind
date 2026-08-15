# ADR-005: Reciprocal Rank Fusion Over Learned Sparse Retrieval

**Status:** Accepted
**Date:** 2025-03-11
**Deciders:** SourceMind Engineering

---

## Context

The SourceMind search system combines two complementary retrieval methods:

1. **Semantic search** — pgvector cosine similarity over `text-embedding-3-large` (3072-dim)
   - Strength: finds conceptually similar memories even with different wording
   - Weakness: poor at exact-keyword recall, sometimes misses obvious matches

2. **Keyword search** — PostgreSQL `tsvector` BM25 ranking
   - Strength: precise recall for specific terms, names, acronyms
   - Weakness: misses paraphrases and synonyms

Both lists must be merged into a single ranked result. Several strategies exist.

---

## Options Considered

### Option A: Weighted linear combination
`score = α * semantic_score + (1-α) * keyword_score`

Rejected: requires calibrating α on labeled data. Scores from pgvector (cosine similarity 0.0–1.0) and BM25 (unbounded) are not comparable. Normalization adds complexity.

### Option B: Learned sparse retrieval (SPLADE, BM25S with learned weights)
A transformer model learns to produce sparse representations that combine BM25 and dense retrieval.

Rejected for Phase 2:
- Requires training on domain-specific data (we have no labeled data yet)
- Adds another ML inference step to the latency budget (~50-150ms on GPU)
- Overkill for typical workspace sizes (<10k memories)
- Can be introduced in Phase 4 once usage patterns are established

### Option C: Reciprocal Rank Fusion (RRF) ✅ Selected

`rrf_score(d) = Σ 1 / (k + rank_i)` where `k=60` (Cormack et al., 2009)

RRF is parameter-robust: the `k=60` constant is effective across retrieval scenarios without calibration. It only requires relative rank, not absolute score values, making it robust to the non-comparability problem.

---

## Decision

Use RRF with `k=60` as the merge strategy. Each retrieval method returns up to 50 candidates. The merged list is truncated to the requested `limit` (default 10, max 100).

**Implementation:**
- `hybrid.py:_rrf_merge()` — pure Python, no external dependencies
- Both retrieval methods run in parallel via `asyncio.gather()`
- PostgreSQL `content_tsv` (generated tsvector column) serves as the BM25 index

---

## Performance Targets

| Metric | Target | Strategy |
|--------|--------|----------|
| p50 latency | < 200ms | Parallel async, embedding cache |
| p95 latency | < 500ms | HNSW ef_search=40, connection pool |
| Query embedding cache hit rate | > 70% | Redis 7-day TTL |

If latency targets are not met on large workspaces (>100k memories), the mitigation is to:
1. Reduce `ef_search` on the HNSW index (trade recall for speed)
2. Pre-filter by workspace before vector scan (already implemented)
3. Introduce result caching for common queries (TTL: 60 seconds)

---

## Consequences

- Positive: No training data required, works from day one
- Positive: Simple to understand and debug
- Positive: k=60 is robust; no hyperparameter tuning needed
- Negative: Not adaptive — cannot learn user preferences over time
- Negative: Ignores absolute relevance scores (a score of 0.95 and 0.51 rank equally at positions 1 and 2)
- Future: Replace with fine-tuned bi-encoder (Phase 4) once we have user interaction signals
