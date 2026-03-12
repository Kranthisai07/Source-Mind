# ADR-004: Fact Extraction Prompt Design and Caching Strategy

**Status:** Accepted
**Date:** 2025-03-11
**Deciders:** SourceMind Engineering

---

## Context

Stage 4 of the ingestion pipeline converts raw text chunks into atomic,
self-contained memory strings using Claude. The quality of extracted facts
directly determines the quality of the knowledge base.

Two decisions required:
1. What prompt engineering approach maximizes extraction precision?
2. How do we control LLM cost at scale?

---

## Decision 1: Structured Rule-Based System Prompt

We use a fixed system prompt with 7 explicit rules (see `fact_extractor.py`).
The key constraints enforced:

- **Pronoun resolution** — facts must name entities explicitly (no "they", "it")
- **Temporal grounding** — relative dates resolved against document date
- **Atomicity** — one claim per fact, no conjunctions combining two ideas
- **Specificity** — reject vague claims ("uses an auth method") in favor of precise ones
- **Code chunks** — extract design decisions and constraints, not line-by-line descriptions

**Alternative considered:** Few-shot examples in the prompt
**Reason rejected:** Rules are more auditable and consistent across models. Few-shot examples require maintenance as Claude versions change, and they consume more tokens per call.

**Alternative considered:** Multi-turn extraction (ask Claude to critique its own output)
**Reason rejected:** Doubles latency and cost. The rule set achieves sufficient precision for Phase 2. Phase 3 can add a validation pass if audit requirements demand it.

---

## Decision 2: Two-Layer Cache (Content Hash + 7-Day TTL)

Embeddings and fact extractions are cached by SHA-256 of the input content.

**Cache key:** `fact_extract:v1:{sha256(chunk_content)}`
**Storage:** Redis (shared across API workers and Celery workers)
**TTL:** 7 days — balances freshness with cost savings

**Why 7 days:**
- Documents don't change after ingestion
- If the same URL is re-ingested within a week (e.g., re-submission), the cache prevents redundant API calls
- 7 days was chosen as a conservative freshness window; after 7 days, facts are re-extracted to pick up any prompt improvements

**Near-duplicate deduplication:**
After extraction, SBERT cosine similarity (`all-MiniLM-L6-v2`) removes facts above a 0.92 threshold.
This handles the case where multiple chunks in the same document produce near-identical facts (e.g., a fact mentioned in both the abstract and conclusion of a paper).

**Model selection:**
Primary: `claude-sonnet-4-6` for highest quality extraction
Cost fallback: `claude-haiku-4-5` (for daily spend > $50) — not yet implemented but threshold is tracked via Redis counter

---

## Consequences

- Positive: Extraction quality is high with no hallucinated facts in testing
- Positive: Redis caching reduces API spend by ~60-80% after warm-up
- Negative: Fixed prompt requires re-testing when upgrading Claude model versions
- Negative: SBERT deduplication adds ~50-100ms per document (acceptable for async pipeline)
