# Decisions Log

Running record of decisions taken, what evidence drove them, and what was
deliberately left undone.

> **Note on this file's origin.** It was created on 2026-09-02. Earlier work
> referred to a decisions log, but no such file existed in the repository — the
> record lived only in conversation. Entries below cover decisions whose
> evidence is reproducible from the codebase, the evaluation data, or the
> database. Where a prior option's original wording is not recoverable, that is
> stated rather than reconstructed from memory.

---

## D-001 — Skip fact extraction for already-atomic thin content

**Status:** Done (2026-09-02)
**Referred to previously as "Option 1".**

### Problem

Retrieval on the 300-item evaluation corpus was dominated by five
`facebook/react` commits that appeared in the top-5 for query after query,
regardless of which commit the query asked about. Diagnosis ruled out the
obvious explanations in turn:

- **Not information loss.** Identifiers survive extraction. All five attractors
  kept their commit hash and PR number verbatim; across 20 random react
  commits, 16/20 preserved every identifier and 19/20 preserved the hash.
- **Not fragmentation at fixed k.** 83% of queries returned five *distinct*
  artifacts in the top-5.
- **Not k calibration.** Widening k from 5 to 20 helped NaiveRAG (+141%
  relative) more than SourceMind (+91%). For 108 of 176 misses the correct
  artifact was absent from the top 20 entirely.
- **Not the RRF smoothing constant.** A fusion constant only reorders results
  near the cut; it cannot reach an artifact that is not in the top 20 at all.

The actual mechanism: four of the five attractors had **title-only sources**
(72–99 characters). The extraction prompt requires every fact to stand alone
with pronouns resolved (Rules 1, 2 and 7), so a one-line commit title becomes
two or three facts that each restate the full context, differing only in an
identifier:

```
"The GitHub repository 'facebook/react' contains commit 561ed529b3a6 …"
"The GitHub repository facebook/react received commit 75ae73e68c02 …"
```

Measured on the stored embeddings, those two sit **0.0810** apart in cosine
distance — inside the 0.15 conflict threshold. The identifier contributes
almost nothing to the vector; the boilerplate dominates it, and the boilerplate
is what every similarly-shaped query also encodes.

### Decision

Before Stage 4, if a document is a **single chunk**, under **150 characters**,
and contains **no sentence boundary** (`[.!?]` followed by whitespace and a
capital), skip Claude-based extraction and embed the original content verbatim
as one memory.

Implemented in `FactExtractor.extract()`, so the worker's data flow is
unchanged: a skipped document returns a one-element `ExtractionResult` and
flows through embedding, attribution and relation detection identically to a
one-fact extraction.

### Evidence it worked

Pairwise cosine distance among the four thin attractors, before → after:

| Pair | Before | After |
|------|--------|-------|
| 561ed529 vs 75ae73e6 | 0.0810 | **0.3617** |
| 926fa855 vs 561ed529 | 0.0879 | **0.3188** |
| 926fa855 vs 75ae73e6 | 0.1231 | **0.3261** |

Minimum across all six pairs: **0.0810 → 0.2986**. Mean **0.3323**. Pairs still
inside the 0.15 conflict threshold: **0 of 6**.

The 2647-character case still produces 22 facts across 5 chunks, unchanged.

### The threshold is intentionally broad — re-validate for other corpora

The rule catches *any* short single-sentence document, not just commit titles.
That is correct for this corpus: GitHub commits, PRs, issues and discussions
with thin bodies genuinely are atomic, and decomposing them produces the
boilerplate measured above.

**It was validated only against that corpus shape.** A corpus of short
standalone assertions — chat logs, FAQ entries, changelog lines, support
tickets — would route almost entirely around extraction. That may still be
desirable, but it is a different question and should be re-measured rather than
assumed. Anyone adopting SourceMind for a materially different corpus should
re-validate `_THIN_CONTENT_CHARS` and the sentence-boundary heuristic.

### Two distinct consequences for the extraction cache

These are related but not the same fact, and both hold:

1. **The skip bypasses the cache.** It returns before `_extract_from_chunk`,
   which is where the Redis lookup lives, so thin content never consults the
   cache. Intended: skipping is cheaper and more deterministic than a hit.
2. **Cache entries written for thin content before this change are now
   orphaned.** Their keys are never read again, and they expire naturally on
   their 7-day TTL. Harmless — a hit would have returned exactly the
   boilerplate this change exists to stop producing — but it means the fix has
   no interaction with historical cache state rather than invalidating it.

Caching for content that still extracts is **unaffected**, and this is covered
by a test: a cold call consults Redis, calls the API once and writes the
result; a warm call serves the stored value without touching the API.

### Test-fixture change, stated plainly

Eight existing tests failed because their fixtures were short single sentences
that now take the skip path. Their fixtures were widened to multi-sentence
content.

This is deliberately distinguished from "the fix broke tests, so the tests were
changed to match": those eight verify **retry logic, parse-failure handling and
cache mechanics**, none of which has anything to do with content length. The
fixtures being short was incidental, not load-bearing to what each test claims
to verify. The skip behaviour itself is covered separately by
`tests/unit/ingestion/test_thin_content_skip.py`.

---

## Deferred — not done, with reasons

### "Option 2"

Deferred. The original text of this option is **not recoverable** — it was
described in conversation before this file existed, and is not reconstructed
here rather than risk misstating it. It should be re-specified before being
picked up.

### Retirement false positives in `detect()`

`updates` relations retire their target (`current_version = FALSE`), and
retired memories are excluded from search. Audit of all 40 retirements in the
run-2 corpus:

- **13 (32%)** cite *different* PR/issue numbers on each side — outright false
  positives, e.g. `PR #94159` retiring `PR #94162`. All at confidence **0.95**,
  so raising the 0.85 threshold would not catch them.
- **15 (38%)** are near-duplicate text — defensible deduplication, but recorded
  as supersession.
- **0 (0%)** are cross-repository, so restricting `updates` to same-repository
  pairs would prevent **none** of them. `detect()` skips same-batch candidates,
  so it only ever compares across documents, and within one repo those share
  near-identical phrasing.

Two artifacts lost every memory this way and became permanently unretrievable.
D-001 addresses the root cause for thin content (the boilerplate that made
these pairs look identical), so this should be **re-measured after D-001 is in
the corpus** before designing a separate fix.

### `uq_documents_workspace_sha256` is not a partial index

The dedup query filters `deleted_at IS NULL`; the unique index does not. A
soft-deleted document therefore permanently blocks re-ingesting that content,
surfacing as an unhandled 500. Either the index needs `WHERE deleted_at IS
NULL` or the dedup query must stop filtering on it — but they must agree.

### Conflict detection is unreachable through bulk ingestion

`_maybe_create_conflict` returns early when both memories share a contributor,
and every ingested memory is attributed to the authenticated API caller. Same
root cause as `attribution_accuracy`'s exclusion from the evaluation:
ingestion produces single-contributor, single-edit memories.

### Retrieval latency scaling

SourceMind p50 rose from 593 ms at 234 memories to ~1040 ms at ~1000. Not
investigated.

### Scripts must run from `apps/api`

Python invoked from the repository root does not load `apps/api/.env`, so
`DATABASE_URL` falls back and name resolution fails. This cost time three times
in one session.
