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

> **Superseded in part by D-005.** This finding was correct about D-001 but
> too general as a statement about the system. Run 4 showed the binding
> constraint was that the BM25 arm returned nothing for 294 of 300
> questions - the system was arm-bound, not volume-bound. Read the section
> below with that correction in view.

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

## D-004 — Query-adaptive fusion weighting, plus the keyword arm it needed

**Status:** Done (2026-09-02). Implements the Option 2 specification below,
with one documented departure from it.

### What the investigation found before anything was written

The specification said to change the RRF fusion weights and nothing else -
explicitly not BM25, not the embedding stage, both retrievers left as they are.
Reading `hybrid.py` first, as the work order required, turned up the reason
that could not work on its own.

**The keyword arm was returning zero rows for 294 of the 300 evaluation
questions** - 100 of 100 pull requests, 99 of 100 commits, and all five
attractor commits. The cause is `plainto_tsquery`, which joins terms with
AND. The real question

> What did commit 926fa8554175 change in facebook/react?

becomes `'commit' & '926fa8554175' & 'chang' & 'facebook/react'`, and the
conjunction fails whenever the stored memory lacks any one filler word, even
though the hash itself matches exactly. Fusion weighting cannot recover from
that, because zero multiplied by any weight is still zero.

That was measured rather than argued. Four fusion variants were run over
identical candidate lists:

| variant | 5 attractors | 95-query sample |
|---------|--------------|-----------------|
| A - baseline, equal weights, AND keyword arm | 1/5 = 0.200 | 18/95 = 0.1895 |
| **B - weighted 0.7/0.3, AND keyword arm (the spec as written)** | **1/5 = 0.200** | **18/95 = 0.1895** |
| C - weighted 0.7/0.3, identifier keyword arm | 5/5 = 1.000 | 64/95 = 0.6737 |
| D - **equal** weights, identifier keyword arm | 5/5 = 1.000 | 63/95 = 0.6632 |

B is identical to A on every query tested - the same 18 hits, not approximately
the same number. D against A isolates the keyword arm at **+47.4pp**; C against
D isolates the reweighting the specification actually asked for, at **+1.05pp**,
one query in 95.

So the gain is almost entirely in the part the specification excluded. This was
reported and the scope decision taken deliberately before implementing.

### What was built

Both mechanisms, C:

1. **Identifier detection** (`_extract_identifiers`) - a cheap regex over
   the raw query, no model call, matching abbreviated and full commit hashes and
   `#NNNNN` issue/PR/discussion references.
2. **An identifier-aware keyword arm** - when the query carries identifiers,
   `_keyword_search` searches on those alone, OR-ed, under the
   `simple` configuration so they are not stemmed. Without identifiers the
   original `plainto_tsquery` AND behaviour is used, unchanged.
3. **Weighted RRF** - `_rrf_merge` takes `w_semantic` and
   `w_keyword`, both defaulting to 1.0, so the six pre-existing tests and
   any other caller get exactly the previous behaviour.

The weights are 0.6 / 1.4 rather than 0.3 / 0.7. Same ratio, scaled to sum to
2.0 to match the `1.0 + 1.0` default, so result scores stay on one scale
whether or not the boost fired. RRF ordering is invariant under scaling both
weights by a common factor, so this ranks identically to the ratio that was
measured.

### The regex detail that would have been a latent bug

The obvious pattern `\b[0-9a-f]{7,40}\b` also matches ordinary English
words built only from the hex letters - **defaced**, **effaced**, **deadbeef** -
which would fire the keyword boost on prose. Requiring at least one digit *and*
at least one a-f letter rejects all of them and still accepts
`926fa8554175`. This corpus happens to contain none of those words, so
the naive form would have passed every test here and broken on someone else's
data. There is a test for it.

### Measured, through the live code path

Not the simulation - `hybrid_search` itself, against the run-3 workspace,
with the change in the working tree and then stashed for the before figures.

**The five attractor commits, which is the direct proof it addresses D-002's
diagnosed mechanism:**

| commit | before | after |
|--------|--------|-------|
| 926fa8554175 | absent from top 20 | **rank 1** |
| 75ae73e68c02 | absent from top 20 | **rank 1** |
| cafd63bcf755 | rank 10 | **rank 1** |
| 561ed529b3a6 | rank 12 | **rank 1** |
| 142cfde89eda | rank 1 | rank 1 |

recall@5 on the five: **0.200 -> 1.000**.

**95-query sample, all four artifact types, real before/after:**

| type | n | before | after | delta |
|------|---|--------|-------|-------|
| commit | 25 | 0.240 | 0.920 | +68.0pp |
| pull_request | 25 | 0.280 | 0.920 | +64.0pp |
| issue | 25 | 0.080 | 0.520 | +44.0pp |
| discussion | 20 | 0.150 | 0.250 | +10.0pp |
| **overall** | **95** | **0.1895** | **0.6737** | **+48.4pp** |

46 queries gained. **Zero queries regressed** - no query that hit before missed
after.

### Against the expected magnitude: it does NOT match, and that is not a win

The specification predicted +2 to +7.5pp, from the DAT paper. The measured
result is +48.4pp. **The literature did not predict this and it should not be
read as confirming it.**

DAT measures reweighting two functioning retrievers against each other. That is
variant C-minus-D, and it came in at **+1.05pp** - below the cited range, not
above it. The +48.4pp headline is variant D, which is not reweighting at all: it
is repairing an arm that was returning nothing. A different intervention with a
different mechanism, and the cited range never applied to it.

Stated plainly: the technique the specification named delivered about a fiftieth
of the measured gain. The investigation the specification demanded before
implementing is what found the rest.

### Caution test

Required by the specification: confirm no regression where dense retrieval
already works. The evaluation dataset cannot supply it - **all 300 questions
contain an identifier**, so the trigger fires on every one and there is no
untriggered class in it to test.

Fifteen conceptual queries were written against real corpus subject matter
instead, each asserted identifier-free, and the full top-10 captured before and
after: ids, ranks, match types and scores to ten decimal places.

**126 result rows compared, zero differences.** The change is inert for
non-identifier queries, as the code path requires - no identifiers means the
keyword arm takes the original branch and `_rrf_merge` is called with its
defaults.

Worth being clear about what this does and does not establish. It confirms the
additive-only property. It does **not** test the paper's warning that a BM25
boost can hurt when dense retrieval is already strong, because that warning
applies to queries where the boost fires, and this corpus has no
identifier-bearing query where the dense arm is already strong enough to be
damaged. On a corpus with both properties, that risk is still open.

### Not yet run

No production deployment and no 300-item evaluation. Three multi-hour runs have
already been spent; whether a fourth is worth it is a separate decision, taken
with these numbers in hand.

---

## D-005 — Evaluation run 4: hypothesis CONFIRMED, and D-002 resolved

**Status:** Measured 2026-09-03. Resolves the investigation opened by D-002.

### The prediction, recorded before the run

> Expect Knowledge Retention (like-for-like) to improve substantially over run
> 3's 0.2769, given the isolated measurement shows recall@5 on the specific
> attractor commits going from 0.200 to 1.000, and a 95-query sample improving
> from 0.1895 to 0.6737 with zero regressions. This is the first fix targeting
> the mechanism D-002 identified as dominant, rather than a fix at the ingestion
> layer.

### Result: confirmed, by a wide margin

|                          | run 1 | run 2 | run 3 | **run 4** |
|--------------------------|-------|-------|-------|-----------|
| ingested / 300           | 244   | 251   | 260   | **251** |
| failed                   | 56    | 49    | 40    | **49** |
| memories                 | 1116  | 1158  | 1107  | **1156** |
| retention raw            | 0.260 | 0.250 | 0.240 | **0.7033** |
| retention like-for-like  | 0.3197 | 0.2988 | 0.2769 | **0.8406** |
| naive_rag raw            | 0.3133 | 0.3133 | 0.3133 | 0.3133 |
| naive_rag like-for-like  | 0.2951 | 0.2869 | 0.2962 | 0.2948 |
| role scope engineer      | 0.9777 | 0.979 | 0.9793 | 0.9787 |
| role scope manager       | 0.392 | 0.3587 | 0.3768 | 0.443 |
| latency p50 / p95 (ms) [*] | 1063/1082 | 1038/1063 | 1097/1153 | 155.7/174.2 |

[*] The latency row is **not a valid code comparison** - see the corrected
latency section below. It is dominated by deployment state, and unchanged run-2
and run-3 data now measures ~8x faster than the figures shown here.

NaiveRAG raw retention was **0.3133 (94/300) in all four runs**. The control has
now held four times, so movement in the SourceMind column is attributable to the
changes rather than to drift in the dataset or harness.

Like-for-like retention went **0.2769 -> 0.8406**, and SourceMind moved from
trailing NaiveRAG (0.2769 vs 0.2962 in run 3) to leading it by 54.6 points.

### The clean comparison is run 4 against run 2, not run 3

Run 4 carries two changes relative to run 3 - D-003 reverted the thin-content
skip and D-004 added identifier-aware fusion - so run 3 is not a single-variable
baseline. Run 2 is.

Reverting the skip returned ingestion to run-2 behaviour, and the corpora came
out nearly identical: **251 documents and 1156 memories in run 4 against 251 and
1158 in run 2**, a difference of two memories. With the ingestion side held
essentially constant, like-for-like retention went **0.2988 -> 0.8406, +54.2
percentage points**, and the difference is retrieval-side.

That is corroborated by the controlled experiment run before deployment, where
only `hybrid.py` changed and the corpus was held fixed (the run-3
workspace): 0.1895 -> 0.6737 on a 95-query sample, 46 queries gained, zero
regressed.

### THE RESOLUTION: D-002's finding was right, and its framing was too narrow

D-002 concluded that retrieval here is *volume-bound, not discrimination-bound*,
after the thin-content fix improved embedding separation without improving
recall. Run 4 shows that conclusion was correct about D-001 and wrong as a
general statement about the system.

The binding constraint was neither volume nor embedding separation. It was that
**the BM25 arm was returning nothing at all** for 294 of 300 questions, so the
system was effectively running single-arm dense retrieval while reporting itself
as hybrid. No amount of ingestion-side tuning could reach that, which is why
three consecutive ingestion-layer runs moved the metric by less than five points
in either direction.

Restated for the record: *retrieval was arm-bound*. One of the two retrievers
was silently inert, and neither embedding quality nor corpus size was the limit
while that was true.

### The caveat that matters most, stated plainly

**This dataset is unusually favourable to this fix, and the +54pp should not be
extrapolated to conversational queries.**

All 300 questions are generated in the form "What did commit 926fa8554175 change
in facebook/react?" or "What was the purpose of PR #36554 in X?" - every single
one carries an exact identifier that also appears verbatim in the stored memory
text. That is the best possible case for exact-match lexical retrieval, and it
is the reason the identifier trigger fires on 100% of this benchmark rather than
on the minority of queries it would fire on in real use.

What run 4 establishes is that the hybrid architecture works when both arms
function, and that the arm was broken. What it does **not** establish is a
+54-point improvement for a user asking "why did we stop transpiling computed
property names?" - a query with no identifier in it takes the unchanged code
path, and its retrieval quality is exactly what it was in run 3. The caution
test measured this directly: 126 result rows over 15 conceptual queries, zero
differences.

A benchmark whose queries all contain a primary key rewards exact-match
retrieval heavily. That is a property of the dataset, not a property of the
system, and a future dataset should include conversational queries so the
untriggered path is measured rather than assumed.

### Latency: the drop is NOT ours, and the earlier explanation is withdrawn

**This section originally attributed roughly 3x of the run3 -> run4 latency drop
to the keyword arm, offered as a likely mechanism. It was measured on
2026-09-03 and is false. The correction follows; the original claim is not
preserved as if it still stood.**

**1. The fix costs approximately nothing.** Measured in-process - no HTTP, no
auth, no API round trip, embeddings pre-warmed into Redis so no OpenAI call sits
inside a timed span - over 20 queries x 5 repetitions against the same database
the deployed API uses. The strict isolation runs the *same* 10 identifier
queries down both code paths, which needs no source change:
`_keyword_search(identifiers=None)` is the pre-D-004 behaviour and
`_rrf_merge` with default weights is the pre-D-004 merge.

| path | median | p95 |
|------|--------|-----|
| new (D-004) | 244.77ms | 249.18ms |
| old (pre-D-004) | 243.81ms | 246.81ms |
| **delta** | **+0.96ms** | +2.37ms |

+0.96ms on a ~244ms operation is 0.4%, and it is **smaller than the measurement
noise** - the spread between queries within a single set is ~7ms. The
fix-authored code bounds it directly: `_extract_identifiers` 0.0107ms and
the weighted `_rrf_merge` 0.1207ms, against 0.1263ms for the unweighted
merge. The weighted merge timed marginally *faster*, which is simply noise. Total
fix cost: 0.13ms.

**2. The keyword-arm mechanism is refuted.** The arm costs **97.71ms on the new
identifier path against 97.51ms on the old AND-conjunction path**. There is no
difference. The claim that the old multi-term `tsquery` was expensive to
evaluate was wrong.

**3. The drop is not code-attributable at all.** The run-2 and run-3 workspaces
were re-benchmarked through the deployed API, same queries, same endpoint, same
data that produced the original figures:

| workspace | recorded at the time | measured 2026-09-03 |
|-----------|----------------------|---------------------|
| run 2 | 1037.6ms p50 | **132.6ms** |
| run 3 | 1097.0ms p50 | **127.3ms** |
| run 4 | 155.7ms p50 | 149.3ms |

Unchanged data now measures 8x faster than it did at the time. Run 3's workspace
is currently **faster than run 4's** (127.3ms against 149.3ms), so D-004 did not
improve latency - if anything run 4's larger corpus is marginally slower.

The ~1030ms figures in runs 1-3 also look like a fixed cost rather than load:
run 1 ranged 1029.44-1084.26ms and run 2 1029.96-1068.07ms, a hard floor near
1030ms with only ~55ms of spread. Network variance does not look like that. The
cause was not identified - the candidates are Railway deployment or container
state, and confirming it would need Railway-side metrics this session cannot
reach.

**4. Consequence for the record: the latency row in the run 1-4 comparison table
is not a valid code comparison and must not be read as one.** It is dominated by
deployment and infrastructure state between runs. Any future latency claim needs
the in-process isolation used here, or two runs on one deployment.

**Incidental finding, unexplained:** attribution enrichment
(`include_attribution=true`, which the harness always sends) costs +28.1ms
on run 4's workspace against +4.0ms on run 3's, for similar memory counts. Not
chased.

### Role-scoped retrieval

Engineer scope is unchanged at 0.9787 (0.9777 / 0.979 / 0.9793 across runs 1-3),
as expected: it was never the constraint. Manager scope improved 0.3768 ->
0.443, its best figure across four runs, though it remains the weakest metric
and below NaiveRAG's 0.596.

### Reconciliation

`total=300, ingested=251, recovered_by_retry=3, failed_ingestion=49`.

The 49 failures are almost entirely `no_memories` - extraction returned
nothing rather than the pipeline erroring - matching run 2's 49 exactly in count
and class.

**`recovered_by_retry=3` is the first non-zero value this field has ever
had.** The retry path was silently dead for the whole of run 3 (the
.env-relative-to-cwd trap meant Celery could never reach the broker), and the
fix committed with run 3 is confirmed working: three documents that would have
been counted as failures were recovered.

### Measurement faults in this run, disclosed

- **One search of 300 died with a network `ConnectTimeout`** and was scored
  as a miss. Maximum impact 0.33pp raw / 0.40pp like-for-like, and the bias is
  conservative - it can only understate SourceMind. The independent like-for-like
  re-query returned the same 211 hits, so the reported figure reproduces.
- **`stuck` peaked at 4 documents** during ingestion, resolved by
  reconciliation.
- The hits were spot-checked against real content rather than trusted from the
  id map: sampled queries return memories that visibly carry the right
  identifier, with `match_type` of `keyword` or
  `semantic+keyword` exactly where the fix should fire. The id map is not
  degenerate (mean 4.61 memories per document, max 22).

### What this closes and what it leaves open

Closed: the D-002 investigation, and the question of whether retrieval could be
improved without trading away corpus size. It can, and was.

Open: the manager role scope (0.443, still behind NaiveRAG); attribution
accuracy, still excluded by design pending the wiring gap; conflict detection,
still lacking labelled negatives; and the untested caution from D-004 - whether
a BM25 boost hurts where dense retrieval is already strong - which this corpus
still cannot answer. Cross-encoder reranking remains the sequenced next step.

---

## Deferred — not done, with reasons

### Option 2 — query-adaptive fusion weighting

**Status: IMPLEMENTED by D-004 (2026-09-02).** Kept here because the
specification is what the implementation was built and judged against - D-004
records where it held and where it did not. The trigger and location below were
both correct; the expected magnitude was not, and the constraint against
touching the keyword arm made the change a measured no-op on its own.

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
