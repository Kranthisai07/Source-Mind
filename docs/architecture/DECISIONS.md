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

**Status:** **REVERTED by D-003 (2026-09-02).** The diagnosis below is still
accurate and the measurements still stand; the *behaviour* it describes is no
longer in the codebase. Read this entry as the record of a correctly-identified
mechanism, not as a description of how ingestion works today.
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

### Open question this leaves — since ANSWERED by D-003

Whether to keep the thin-content skip is now genuinely open. It is correct on
its own terms and fixed a real defect, but on this corpus it costs recall.
Keeping it, reverting it, or making it conditional on corpus density should be
decided deliberately rather than by inertia.

**Resolved:** D-003 reverted it outright, and moved the discrimination work to
the retrieval layer where it does not trade against corpus size.

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

## D-003 — D-001's skip reverted; discrimination moves to the retrieval layer

**Status:** Done (2026-09-02)

### Decision

The thin-content skip introduced in D-001 is **removed outright** — no flag, no
dormant code path. Thin documents go back through normal multi-fact extraction
and produce two or three memories each, as before D-001.

### Why

D-001 correctly identified a real embedding-collapse problem and demonstrably
fixed it: cosine separation among the attractor commits went 0.0810 → 0.2986
locally and 0.0810 → 0.2747 on the deployed worker, with every pair moved
outside the 0.15 conflict threshold.

D-002 then showed the fix cost more than it gained. Skipping extraction traded
memory COUNT for memory DISTINCTNESS, and on this corpus the count mattered
more: like-for-like retention fell 0.2988 → 0.2769, and on the 241 items common
to runs 1 and 3 SourceMind went from 0.3197 to 0.2905 — from beating NaiveRAG
by 2.5 points to tying it exactly.

The discrimination problem is real. Ingestion is the wrong layer to solve it
at, because every lever there trades against corpus size. It belongs at the
**retrieval** layer, where discrimination can improve without removing anything
from the index — see the Option 2 entry below.

### No flag, deliberately

A disabled flag would be worse than removal. `ENABLE_THIN_CONTENT_SKIP = False`
sitting in the code is a trap: a future reader has to dig through this log to
learn why it exists and whether flipping it is safe, and nothing tests that
path. This session has spent considerable effort removing exactly that class of
latent state — dead code, stale comments, misleading defaults — and adding a
fresh instance to preserve an option would contradict the reasoning.

D-001 and D-003 together hold the complete record: the mechanism, the
measurement, and the reason for reverting. Anyone who needs the behaviour back
for a denser corpus can rebuild it correctly, and better informed than we were —
by then the Option 2 fusion work may have made it unnecessary, or the two may
need to compose differently than either would have alone.

### What was kept

- **The diagnosis and its measurements**, in D-001 above. Note these were
  produced by throwaway scratchpad scripts, not committed tests — they are a
  written record of what was measured, not an executable regression guard, and
  they do not need to be executable to be trustworthy.
- **The retirement false-positive audit** in the Deferred section (13 of 40
  confidently wrong at 0.95 confidence, 0 cross-repository).
- **One test**, `test_cache_is_read_and_written_for_extracted_content`,
  relocated into `test_extraction_empty_vs_failed.py`. It verifies that
  extraction's Redis cache still reads and writes, which is true independently
  of the skip and worth keeping. The other four tests in the deleted
  `test_thin_content_skip.py` asserted the reverted behaviour and went
  with it.
- **The eight widened test fixtures**, left widened. Re-checked individually:
  each verifies retry, parse-failure or cache mechanics, and multi-sentence
  content exercises those paths unambiguously. They are the better fixtures on
  their own merits, so re-narrowing them would be churn. The comment that
  pointed at the deleted file was corrected.

---

## Deferred — not done, with reasons

### Option 2 — query-adaptive fusion weighting

**Status: SPECIFIED, ready to scope for implementation.** Both previously-open
sub-questions now have evidence-backed answers rather than guesses.

**This is researched prior art, not original reasoning.** The technique is
established and named in the literature — "Dynamic Weighted Reciprocal Rank
Fusion", "Adaptive RRF with IDF Weighting", and "DAT (Dynamic Alpha Tuning)"
(Hsu & Tzeng, 2025) all describe the same core idea: adjust the relative weight
given to BM25 versus semantic search **per query**, instead of applying one
fixed blend to every query. It is a standard production feature elsewhere —
Qdrant supports per-request alpha weighting between sparse and dense retrievers,
and Elasticsearch supports boost parameters on individual sub-queries in the
same request. Applying it to our own RRF implementation is adopting a known
pattern, not inventing one.

**Trigger mechanism.** Query IDF (inverse document frequency), or the simpler
proxy of identifier patterns detected in the query text — regex for issue and PR
numbers (`#\d+`) and commit-hash-shaped tokens. High-IDF, identifier-bearing
queries shift weight toward BM25 exact matching; low-IDF conversational queries
stay weighted toward semantic search, which is today's default behaviour. The
published "vstash" system uses average query-term IDF as exactly this signal,
which independently confirms that identifier-bearing queries are the class this
technique is built for — a commit hash or PR number is about as high-IDF as a
token gets. **Rule-based, not a learned classifier**: the trigger is meant to be
cheap.

**Where it lives.** The RRF fusion step specifically — `hybrid.py`'s merge
logic. Not the embedding or indexing stage, and not either retriever
individually. Both retrievers stay exactly as they are; only the weight given to
each result list changes per query. This answers the first open sub-question,
and it now has independent literature and production precedent behind it rather
than only internal logic.

**Expected magnitude, from the literature and not a promise.** The DAT paper
reports +2 to +7.5 percentage points on Precision@1 and MRR, measured
specifically on "hybrid-sensitive" queries — the subset where BM25 and dense
retrieval actually disagree, which is precisely the attractor-commit scenario.
Real, bounded, and non-trivial, but not transformative. Expectations should be
set there before implementing.

**Caution that must be tested for.** One paper found that fusing BM25 more
strongly into an already-strong dense retriever can *hurt*: where the dense
ranking is already good, extra lexical signal promotes
lexically-similar-but-semantically-irrelevant results. That is not a reason to
avoid the change — our dense retriever is specifically failing on this query
class, which is the whole finding of D-002 — but the implementation must verify
it does not regress queries where dense retrieval currently works, not merely
improve identifier-bearing queries in isolation.

**It satisfies the D-002 constraint.** Unlike D-001, this improves
discrimination without reducing memory count: nothing is removed from the index,
only reweighted at query time. Multi-fact extraction stays exactly as it is.

### Cross-encoder reranking — complementary, deferred

A **separate** technique, not a replacement for the above. The established
production pattern is two-stage: hybrid retrieval (BM25 + dense, fused via RRF)
fetches a wide candidate pool of roughly the top 100, then a cross-encoder that
reads query and candidate together reranks the top 30–50 before the final
result.

The two compose rather than compete. Fusion weighting determines **which
documents reach the candidate pool**; reranking determines **the final ordering
among them**. Neither substitutes for the other, and a reranker cannot rescue a
document that fusion never retrieved — which matters here, since D-002 found the
correct artifact absent from the top 20 entirely for 108 of 176 misses.

Worth doing, not urgent, and it should follow the fusion work rather than
precede it.

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
