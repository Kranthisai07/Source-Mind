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

## D-002 — Consolidated evaluation (run 3): hypothesis REFUTED

**Status:** Measured 2026-09-02. Recorded as a refutation, deliberately.

### The prediction, made before the run

> Expect Knowledge Retention (like-for-like) to improve versus run 2's 0.299,
> driven specifically by improved retrieval discrimination from the
> thin-content fix. Expect ingestion coverage (ingested/300) to hold at or
> above run 2's 251.

Recorded in merge commit `8c3f48c` **before** the run started, so it could be
falsified rather than fitted afterwards.

### Result: coverage confirmed, retention refuted

|                          | run 1 | run 2 | run 3 |
|--------------------------|-------|-------|-------|
| ingested / 300           | 244   | 251   | **260** |
| failed                   | 56    | 49    | 40 |
| memories                 | 1116  | 1158  | **1107** |
| retention raw            | 0.260 | 0.250 | **0.240** |
| retention like-for-like  | **0.3197** | 0.2988 | **0.2769** |
| naive_rag like-for-like  | 0.2951 | 0.2869 | 0.2962 |
| role scope engineer      | 0.9777 | 0.979 | 0.9793 |
| role scope manager       | 0.392 | 0.3587 | 0.3768 |
| latency p50 / p95 (ms)   | 1063/1082 | 1038/1063 | 1097/1153 |

NaiveRAG raw retention was **0.3133 (94/300) in all three runs**, which is the
control: the harness and dataset are stable, so SourceMind movement is
attributable to the changes rather than drift.

For the first time SourceMind **trails** NaiveRAG like-for-like (0.2769 vs
0.2962), having led in both earlier runs.

### The denominator confound was checked, not assumed

Run 3 scores over 260 held items against run 2's 251, and the extra items are
ones that previously failed — plausibly harder. Re-scored on the **241 items
held in both run 1 and run 3**:

```
run 3 sourcemind, common 241 : 0.2905 (70/241)
run 3 naive_rag,  common 241 : 0.2905 (70/241)
run 1 sourcemind, its 244    : 0.3197 (78/244)
```

Composition explains part of the drop (0.2769 → 0.2905 on comparable items) but
not all of it. Against run 1 on a near-identical denominator retrieval genuinely
regressed, and the margin over NaiveRAG went from +2.5 points to exactly zero.

### THE FINDING: retrieval here is volume-bound, not discrimination-bound

The thin-content fix **did** fix the mechanism it targeted. Embedding separation
among the attractor commits went 0.0810 → 0.2986 locally and 0.0810 → 0.2747 on
the deployed worker, taking every pair outside the 0.15 conflict threshold. That
was measured, not inferred, and it held in production.

**Fixing it did not improve recall.** The reason is understood rather than
guessed: skipping extraction for thin content trades memory COUNT for memory
DISTINCTNESS. Run 3 holds more documents (260 vs 251) but fewer memories (1107
vs 1158), because a thin document now contributes one memory instead of two or
three. Better-separated vectors, less surface area to match against — and the
second effect outweighed the first.

So: **more distinct memories to match against matters more, in this system as it
currently stands, than each memory being cleanly separated from its
near-duplicates.** The discrimination bug was real and is fixed; it was not the
binding constraint on recall.

This is more useful to the record than a confirmation would have been. It says
precisely what not to assume next time: that fixing a demonstrated mechanism
moves the headline metric.

### Open question this leaves

Whether to keep the thin-content skip is now genuinely open. It is correct on
its own terms and fixed a real defect, but on this corpus it costs recall.
Keeping it, reverting it, or making it conditional on corpus density should be
decided deliberately rather than by inertia.

### Harness defect found and fixed during this run

The retry-on-disappearance path failed for the entire run:

```
! re-enqueue unavailable: RuntimeError:
```

Two bugs behind one symptom. The message only *looked* empty because it starts
with a newline, which split the log line. The real cause was the
**run-from-repo-root trap, striking a fourth time**: the runner must run from
the repository root for its dataset paths, but pydantic-settings resolves
`.env` relative to the working directory, so it silently used
`redis.railway.internal:6379` — resolvable only inside Railway's network —
instead of the public proxy. Celery could never reach the broker, so run 3's one
vanished task was recorded as `retry_unavailable` rather than genuinely
retried.

Fixed: `runner.py` now loads `apps/api/.env` explicitly at import,
independent of cwd, and `_reenqueue` uses `ignore_result=True`
(nothing reads task results; the stale result backend was pure overhead) with a
fresh connection on retry. Verified working from the repo root — the exact
context that failed.

---

## Deferred — not done, with reasons

### "Option 2" — metadata / structured-identifier boosted retrieval

**Priority raised by D-002.** Still deferred, but it is now the most promising
remaining lever and its requirements are sharper than before.

The original wording is **not recoverable** — it was described in conversation
before this file existed, and is not reconstructed here rather than risk
misstating it. What D-002 establishes about what it must achieve, however, is
recorded precisely:

**It must preserve or increase memory count while improving discrimination, not
trade one for the other.** D-001 improved discrimination by reducing memory
count and recall fell. Any fix that repeats that trade should be expected to
fail the same way.

That points away from *skipping* extraction for thin content and toward keeping
the original multi-fact extraction while fixing boilerplate dilution separately
— boosting structured identifiers (commit hashes, PR numbers, error strings) at
retrieval time, so a fact's distinguishing token carries weight without
shrinking the corpus.

Two things to settle before building it:

1. Whether the thin-content skip (D-001) should be reverted first, since the
   two approaches are alternatives rather than complements on thin content.
2. Whether identifier boosting belongs in the BM25 half of the hybrid, the
   dense half, or the RRF fusion — the diagnosis in D-002 does not distinguish
   these, and guessing would repeat the mistake D-002 documents.

This is architecture work and should be scoped once, deliberately, rather than
attempted as another test-and-rerun cycle. Three multi-hour evaluation runs
have already been spent reaching a well-understood result; the next one should
be spent on a properly scoped change.

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
