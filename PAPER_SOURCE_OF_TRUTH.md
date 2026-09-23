# PAPER_SOURCE_OF_TRUTH.md

Every fact below was read from the real source file in the session that wrote
this document (2026-09-16), on branch `frontend-ci-claude` at commit
`135eb9b410278a738590296d839a478e338a9ae6`. Where a fact could not be verified,
it says **UNVERIFIED — needs direct confirmation** instead of a plausible
answer.

**Step 1 note (archival):** the instruction was to rename the existing file to
`<original_name>.archive.md`. **There was no file to rename.** A search of both
worktrees found no `PAPER_SOURCE_OF_TRUTH*` and no `*.archive.md`. The only
paper-like documents are `SourceMind_Audit_Report.md` and `sourcemind_paper.tex`,
which are untracked owner drafts living in the *other* worktree (`D:\Source Mind`)
that this session is under standing instruction not to modify. **Nothing was
renamed and nothing was deleted.** If one of those was the intended target, say
so and it can be archived deliberately.

---

# SECTION 1 — THE CORE NOVEL CONTRIBUTION, PRECISELY STATED

## 1.1 The contribution, in one paragraph

The defensible contribution is **architectural and combinatorial, not
mathematical**: a team-memory system in which *no model output is ever permitted
to decide which of two conflicting claims is correct*, enforced at four
independent layers — the classification prompt explicitly forbids the model from
judging correctness; the severity function ranks attention but has no
auto-resolution branch at any tier; resolution is an authenticated human action
behind an admin/owner permission gate; and attribution history is append-only by
database trigger, so the provenance record cannot be rewritten to match a later
decision. Alongside it sits a five-signal contributor-attribution scheme that
decomposes authorship of a collaboratively edited memory into character-level,
semantic, temporal, structural and procedural components, normalised so
contributor weights sum to exactly 1.0 with a protected floor for anyone who
made a substantive edit. **This is one contribution with two halves, and the
second half is the weaker claim** — the attribution scheme is an engineering
composition of standard measures, not a new estimator. Presenting them as two
separate novel contributions would be inflation.

## 1.2 Every mathematical formula actually implemented

All from `apps/api/sourcemind/services/attribution/scorer.py` unless stated.

### Constants, read from the live source (lines 97–105)

```python
_W1 = 0.35  # character diff
_W2 = 0.30  # semantic similarity
_W3 = 0.15  # temporal primacy
_W4 = 0.10  # structural (new named entities)
_W5 = 0.10  # explicit approval
_FLOOR = 0.02
_SUBSTANTIVE_CHARS = 10
_APPROVAL_ACTIONS = frozenset({"approved", "merged", "accepted", "approve", "merge"})
```

**Weight-swap check performed.** The module docstring (lines 5–9) lists char
diff 0.35, semantic 0.30, temporal 0.15, structural 0.10, approval 0.10. The
constants match the docstring exactly. No swap is present at this commit. *(The
project has had a documentation/code weight mismatch before; this was re-checked
rather than assumed.)*

### Signal 1 — character diff (`_signal1_char_diff`)

$$s_1 = 1 - \frac{\mathrm{lev}(b, a)}{\max(|b|, |a|, 1)}$$

- $b$ = `content_before`, $a$ = `content_after`.
- **Edge case:** if `before` is falsy (empty or `None`), returns exactly `1.0`
  — initial creation counts as changing 100%.
- **Degraded path:** if the Levenshtein implementation is unavailable or raises,
  it falls back to positional character overlap,
  $\frac{\sum_i [b_i = a_i]}{\max(|b|,|a|,1)}$ over `zip(before, after)`. This is
  a *different measure*, not an approximation of the same one, and the code logs
  it at `error` level.

### Signal 2 — semantic similarity (`_signal2_semantic`)

$$s_2 = \hat{e}(c) \cdot \hat{e}(f)$$

Dot product of L2-normalised SBERT embeddings (`normalize_embeddings=True`), so
this is cosine similarity. $c$ = the contributor's `content_after`, $f$ = the
memory's final content.

- **Edge case:** any exception returns the constant `0.5` and sets a module-level
  `SIGNAL_2_DEGRADED` flag. The code comments note that a constant 0.5 is
  indistinguishable in the output from a genuine measurement of 0.5 — which is
  why it logs at `error`.
- **No clamping.** Cosine can be negative; nothing clamps $s_2$ to $[0,1]$.

### Signal 3 — temporal primacy (`_signal3_temporal`)

$$s_3 = 0.8^{(p - 1)}$$

$p$ = `edit_position` (1-indexed). First author 1.0, second 0.8, third 0.64.
No floor, no cap, no special cases.

### Signal 4 — structural novelty (`_signal4_structural`)

$$s_4 = \frac{|E(a) \setminus E(b)|}{|E(a)|}$$

$E(\cdot)$ = named-entity set, via spaCy NER when importable, otherwise a regex
over CamelCase / ALLCAPS / version strings / known tech terms.

- **Edge case:** if $E(a)$ is empty, returns `0.0` (avoids division by zero).
- **Edge case:** if `before` is falsy, $E(b) = \varnothing$, so $s_4 = 1.0$.
- **Degraded path:** an exception returns `0.0` and sets `SIGNAL_4_DEGRADED`.
  The comment distinguishes this from the regex backend, which is the *designed*
  fallback under ADR-007 and not a degradation.

### Signal 5 — explicit approval (`_signal5_approval`)

$$s_5 = \mathbb{1}[\,\mathrm{lower}(\text{action\_type}) \in A\,]$$

with $A$ = `_APPROVAL_ACTIONS` above. Binary, no partial credit.

### Combination (per edit, line 342)

$$r_e = 0.35\,s_1 + 0.30\,s_2 + 0.15\,s_3 + 0.10\,s_4 + 0.10\,s_5$$

Per contributor $u$, raw score is the **sum over their edits**, not the mean:
$R_u = \sum_{e \in E_u} r_e$. A contributor with more edits accumulates more raw
score before normalisation. Per-signal running *averages* are also maintained,
but only for audit display.

### Normalisation with protected floor (`_normalize`)

1. **Edge case — no contributors:** returns `[]`.
2. **Edge case — $\sum_u R_u = 0$:** every contributor receives exactly
   $1/|U|$ and all per-signal scores are reported as `None`.
3. Otherwise $w_u = R_u / \sum_v R_v$.
4. **Floor loop.** A contributor is *substantive* iff for at least one of their
   edits $\bigl||a| - |b|\bigr| \ge 10$ characters — note this is a **length
   delta**, not an edit distance, so a same-length rewrite of the entire text is
   not substantive. While any substantive contributor has $w_u < 0.02$: set all
   such to exactly `0.02`, then rescale the others by
   $\lambda = \frac{1 - 0.02\,n_{\text{floored}}}{\sum_{v \notin \text{floored}} w_v}$.
   If the non-floored mass is 0, all floored contributors are set to `0.02` and
   the loop breaks.
5. Final renormalisation $w_u \leftarrow w_u / \sum_v w_v$, then `round(w, 6)`.

**Stated honestly:** the floor loop has no proof of termination or of
non-oscillation in the repository, and none is claimed. It terminates in
practice because the floored set grows monotonically per iteration, but that
argument is *not written down anywhere in the codebase* — it is my reading, not
a recorded result. The final rounding to 6 decimals also means the weights sum
to 1.0 only up to rounding.

### Recorded reasoning

The per-signal docstrings state *what* each signal measures. **No recorded
justification exists for the specific weight values 0.35/0.30/0.15/0.10/0.10.**
`DECISIONS.md` (D-001 … D-011) contains no entry deriving or tuning them, and
the code comments do not explain them. They appear to be chosen a priori. **Do
not claim they were empirically tuned.**

### Adjacent formulas (implemented, outside the attribution module)

- **Workspace health** (`services/analytics/workspace.py:54`):
  $H = 0.30\,\text{coverage} + 0.30\,\text{freshness} + 0.25\,\text{conflict} + 0.15\,\text{attribution}$.
- **Hybrid retrieval RRF** (`services/search/hybrid.py`): score contribution
  $\frac{w}{k + \text{rank} + 1}$ with `_RRF_K = 60`; when an identifier pattern
  is detected in the query the arm weights become `_IDENT_W_SEMANTIC = 0.6` and
  `_IDENT_W_KEYWORD = 1.4`, otherwise both arms are equally weighted.
- **Fact dedup** (`services/ingestion/fact_extractor.py:89`): SBERT cosine
  `_DEDUP_THRESHOLD = 0.92`.

## 1.3 Original mathematics, or novel combination?

**Novel combination and architecture. Not original mathematics.** Every
component is standard and long-published: Levenshtein distance, SBERT cosine
similarity, geometric decay, set-difference over NER output, an indicator
function, a fixed-weight linear combination, and Reciprocal Rank Fusion. A paper
claiming a new estimator, a new metric, or a proved property here would be
overclaiming and is very likely to be caught.

What is genuinely defensible:

1. **The decomposition itself** — treating authorship of a collaboratively
   edited memory as five separable signals with a protected floor so that a
   substantive minority contributor cannot be normalised to nothing. The floor
   mechanism is the most original *mathematical* wrinkle, and it is small.
2. **The human-gating invariant, enforced redundantly at four layers** (§3).
   This is a systems-design contribution and is the stronger claim.
3. **The append-only provenance guarantee at the database layer** (§2.3), which
   makes "the attribution record was not rewritten" a property of the schema
   rather than of application discipline.

Honest framing for the paper: *"we combine known measures in a new
decomposition, and enforce a human-gating invariant at four independent layers"*
— not *"we introduce a new attribution algorithm."*

---

# SECTION 2 — SYSTEM ARCHITECTURE, AS ACTUALLY BUILT

## 2.1 Services (verified against `infra/docker-compose.yml`)

| Service | Image | Role |
|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | primary store + vector index + RLS |
| `redis` | `redis:7.2-alpine` | cache, rate limiting, Celery broker |
| `neo4j` | `neo4j:5-community` | attribution graph |
| `redpanda` | `redpandadata/redpanda:latest` | Kafka-compatible event bus |
| `redpanda-console` | `redpandadata/console:latest` | operator UI |
| `api` | built locally | FastAPI |
| `worker` | built locally | Celery worker |

**Two of these are gated off by default** (`core/config.py:191–194`):
`ff_neo4j_attribution = False`, `ff_kafka_events = False`. `ff_conflict_detection
= True`. So Neo4j and Redpanda are *present in compose but not exercised* unless
those flags are flipped. Also default-off: `url_ingestion_enabled = False`,
`slack_memory_commands_enabled = False`.

**Data flow, as implemented:** ingest → `POST /v1/memories` (202 + `job_id`,
requires `workspace_id` as a query parameter and an `Idempotency-Key` UUID-v4
header) → Celery worker → chunk, extract facts (SBERT dedup at 0.92), embed
(OpenAI `text-embedding-3-large`, 3072 dims) → write `Memory` → Stage 7b relation
and conflict detection (§3.1). Retrieval is hybrid semantic + `tsvector` keyword,
merged by RRF.

**UNVERIFIED — needs direct confirmation:** the separate Neo4j/Kafka propagation
paths were not read in this session, because both flags default to `False` and
nothing in scope exercised them.

## 2.2 Data model (verified against the SQLAlchemy models)

- **Memory** — `id, workspace_id, document_id?, parent_memory_id?, content,
  embedding?, content_hash, version, current_version, tags?, category?,
  source_chunk_index?, confidence_score?, importance_score`. Versioning is
  append-only by convention: a new row per edit, linked by `parent_memory_id`,
  with `current_version` as the live flag.
- **Attribution** — `id, memory_id, user_id, contribution_weight,
  char_diff_score?, semantic_score?, temporal_score?, structural_score?,
  approval_score?, trigger_action, edit_id?, created_at`. The five per-signal
  columns persist the decomposition, so a weight can be audited component by
  component.
- **AttributionEdit** — `id, memory_id, editor_id, content_before?,
  content_after, edit_position, action_type, idempotency_key?`.
- **MemoryConflict** — `id, workspace_id, memory_a_id, memory_b_id,
  conflict_type, severity, competing_claim_count, blocks_derivation, status,
  similarity_score, explanation?, resolver_id?, resolved_at?, resolution_note?`.
  **Strictly pairwise.**
- **MemoryRelation** — `id, source_memory_id, target_memory_id, relation_type,
  similarity_score?, confidence?, detected_by`.
- **Workspace** — `id, organization_id, created_by_user_id?, name, slug,
  description?, settings`. **WorkspaceMember** — `id, workspace_id, user_id,
  role, status, …`.

Note `created_by_user_id` on Workspace is **nullable**, and that nullability is
load-bearing — see §5.2.

## 2.3 Database-enforced invariant (verified in `20250311_0001_initial_schema.py`)

```sql
CREATE OR REPLACE FUNCTION prevent_attribution_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'Attribution records are append-only. '
        'INSERT new records instead of modifying existing ones. '
        'Error code: SM091';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER attribution_append_only
BEFORE UPDATE OR DELETE ON attributions
FOR EACH ROW EXECUTE FUNCTION prevent_attribution_mutation();
```

**Guarantee:** no `UPDATE` or `DELETE` on `attributions` can succeed through any
client, including the application's own ORM. History is corrected by inserting a
superseding row. **Scope limit, stated precisely:** the trigger protects
`attributions` only — not `attribution_edits`, not `memories`. A superuser could
still drop the trigger; it binds clients, not the database owner.

## 2.4 Deployed vs. merely written — verified, and the gap is large

**Production runs `main` at `8072e42e285c700b4e350bb44c678ab5caf0db70`.**
Migrations present on that commit, verified via `git ls-tree`:

```
0001_initial, 0002_phase3_additions, 0003_connectors,
0004_artifact_link_document_fk, 0005_conflict_severity
```

**Not in production. Exists only on the unmerged branch:**

- `20260908_0006_security_foundation` — active-membership enforcement, forced RLS
- `20260909_0007_rls_context_hardening` — grant/bootstrap tables, policy rewrite
- `20260916_0008_ownerless_workspace_compatibility`
- The entire role-permission model change to conflict routes (§3.3)

D-009 records "Implemented and locally verified through 0007 on 2026-09-13; **not
deployed**." D-010: "**not deployed**."

**The production deployment therefore does not have the RLS tenancy isolation,
the active-membership gate, or the admin/owner conflict permission model.** Any
paper claim about enforced multi-tenant isolation must be scoped to "implemented
and verified in CI on an isolated PostgreSQL 18 instance", not "running in
production".

---

# SECTION 3 — CONFLICT DETECTION / HUMAN-GATED DESIGN

## 3.1 Detection (verified in `services/memory/relations.py`)

Thresholds, cosine **distance** (lines 49–52):

```python
_SCAN_RADIUS   = 0.25   # scan for candidates
_LLM_RADIUS    = 0.20   # trigger LLM classification
_CONFLICT_RADIUS = 0.15 # trigger conflict check
_MIN_CONFIDENCE = 0.85  # minimum confidence to persist a relation
_CLASSIFY_MODEL = "claude-haiku-4-5-20251001"
_CLASSIFY_MAX_TOKENS = 1024
```

Two passes: top-10 candidates within 0.25; LLM classification only below 0.20; a
conflict is written for pairs below 0.15, from different contributors, with no
existing relation, that the model classifies as `updates`.

**The user prompt, verbatim** — note the explicit prohibition, which is the
enforcement point of the whole design:

> "Two statements from a team knowledge base are shown below. Describe their
> relationship. **Do NOT judge which statement is correct, better supported, or
> more likely to be true. That decision belongs to a human reviewer.**"
>
> …relationship types `updates` / `extends` / `derives` / `unrelated`…
>
> "Second, decide whether the two are in conflict. They conflict when they are
> mutually exclusive claims about the same underlying decision point, meaning
> both cannot be true at once. **Summarise the disagreement factually, stating
> what each side claims, without indicating which one is right.**"
>
> "confidence is how certain you are that this relationship classification is
> correct. **It is NOT a judgement about which statement is true.**"

The system prompt governs output format only (raw JSON, no code fences) — a
comment records that markdown fences previously broke `json.loads`, which
silently produced "unrelated" so that *no relation or conflict was ever written
in any workspace for as long as that code existed*.

## 3.2 Severity (verified in `services/conflict/severity.py`)

```python
_CRITICAL_IMPORTANCE = 0.7
_MEDIUM_IMPORTANCE   = 0.4
_MEDIUM_CLAIMS       = 2
```

$$
\text{severity} =
\begin{cases}
\text{critical} & I > 0.7\\
\text{medium} & I > 0.4 \ \lor\ C \ge 2\\
\text{low} & \text{otherwise}
\end{cases}
$$

$I$ = the disputed memory's `importance_score`, **read, never recomputed** (the
docstring says recomputing would make severity a write path into another table
and could recurse). $C$ = `competing_claim_count`.

$C$ is **derived, not stored by hand**: since `MemoryConflict` is strictly
pairwise, counting per row would pin every conflict at 2. Instead it is the size
of the row's one-hop cluster — every distinct memory named by an unresolved
conflict touching either endpoint. The docstring states the one-hop bound is
deliberate: full transitive closure would chain unrelated decision points.

The module states in capitals: **"NO AUTO-RESOLUTION AT ANY SEVERITY, EVER."**
`blocks_derivation` is the strongest available effect and only marks a conflict
as blocking. This is the current, live version; the docstring records that
critical previously also required three competing claims, which made it
unreachable for the ordinary two-person case.

## 3.3 Who may resolve — verified at head, and it DIFFERS from production

**On this branch (`apps/api/sourcemind/api/v1/conflicts.py`, verified):**

```python
await require_conflict_access(
    db, current_user.user_id, conflict_id, WorkspacePermission.ADMINISTER
)
```

with (`core/dependencies.py:464–469`):

```python
READ:       {viewer, member, admin, owner}
CONTRIBUTE: {member, admin, owner}
ADMINISTER: {admin, owner}
OWNER:      {owner}
```

Membership is checked first so a non-member receives **404**, never learning the
conflict exists; the role check then only runs for real members, whose 403
discloses nothing new.

**Two honest caveats:**

1. **Production differs.** At `8072e42`, `resolve_conflict_endpoint` calls
   `require_conflict_access(db, user, conflict_id)` without a permission
   argument and imports `require_workspace_role` separately. The
   `WorkspacePermission.ADMINISTER` model is branch-only and **not deployed**.
2. **A docstring/code discrepancy exists at head.** The resolve endpoint's
   docstring says "listing, viewing detail and **marking a conflict under review**
   stay open to any workspace member." The `review_conflict` handler
   (line 162) in fact calls `require_conflict_access(..., ADMINISTER)`. **The
   code requires admin/owner; the docstring says any member.** Do not repeat the
   docstring's claim in the paper. Which behaviour is intended is a product
   decision I have not resolved.

---

# SECTION 4 — EVALUATION, EXACTLY AS MEASURED

## 4.1 Numbers, from the results files

All runs: 300 ground-truth items, recall@5, 50 latency queries. NaiveRAG =
Chroma + OpenAI embeddings baseline.

| Run | System | Recall@5 | hits | Role-scope engineer | Role-scope manager | p50 ms | p95 ms | p99 ms |
|---|---|---|---|---|---|---|---|---|
| 1 | naive_rag | 0.3133 | 94/300 | 0.992 | 0.598 | 182.42 | 222.95 | 228.24 |
| 1 | sourcemind | 0.2600 | 78/300 | 0.9777 | 0.392 | 1062.83 | 1082.46 | 1084.26 |
| 2 | naive_rag | 0.3133 | 94/300 | 0.992 | 0.598 | 194.30 | 331.27 | 3947.87 |
| 2 | sourcemind | 0.2500 | 75/300 | 0.979 | 0.3587 | 1037.60 | 1063.46 | 1068.07 |
| 3 | naive_rag | 0.3133 | 94/300 | 0.992 | 0.600 | 221.02 | 560.89 | 794.13 |
| 3 | sourcemind | 0.2400 | 72/300 | 0.9793 | 0.3768 | 1097.03 | 1153.37 | 1437.71 |
| 4 | naive_rag | 0.3133 | 94/300 | 0.992 | 0.596 | 194.00 | 274.65 | 480.92 |
| 4 | **sourcemind** | **0.7033** | **211/300** | 0.9787 | 0.443 | **155.74** | **174.15** | 189.65 |

**Like-for-like, run 4** (`like_for_like_recall` in `results_run4.json`): 49 of
300 documents produced no memories, so SourceMind holds 251. Over those 251:
naive_rag **0.2948** (74/251), sourcemind **0.8406** (211/251). The file itself
records that SourceMind's held-item score is an *independent re-query*, not a
subset of the main run, and that one main-run query failed with a network
`ConnectTimeout` and was scored as a miss.

### Metrics explicitly EXCLUDED, in the runs' own words

- **`attribution_accuracy`** — excluded because "ingestion attributes every
  memory to the authenticated API caller rather than the artifact's original
  author, because `create_initial_attribution()` writes a single contributor at
  weight 1.0 and the 5-signal algorithm runs only on PATCH. The metric would
  therefore report 0.000 for SourceMind by construction, measuring a wiring gap
  rather than attribution quality."

  **This is the single most important honesty constraint in the document.** The
  five-signal algorithm of §1.2 **has never been evaluated end-to-end**, because
  in the ingestion path it does not run at all.

- **`conflict_detection`** — excluded because "dataset lacks labeled negative
  pairs". The same note asserts the human-gated design is "novel … not present
  in comparable systems (Supermemory, Mem0 …)". That comparative claim is a
  *note in a results file*; I have not verified it against those systems and it
  should not enter the paper unchecked.

## 4.2 Trajectory (from `DECISIONS.md`, in order)

- **D-001** — skip fact extraction for already-atomic thin content. Later
  **REVERTED by D-003**.
- **D-002 (run 3, 2026-09-02)** — "hypothesis REFUTED", recorded deliberately as
  a refutation. SourceMind recall fell 0.26 → 0.25 → 0.24 across runs 1–3.
- **D-003** — D-001's skip reverted; discrimination moved to the retrieval layer.
- **D-004** — query-adaptive fusion weighting "plus the keyword arm it needed".
  The investigation found the keyword arm returned **zero rows for 294 of 300**
  questions, because `plainto_tsquery` ANDs every term, so a filler word absent
  from the stored memory nullified an exact hash match. Four fusion variants were
  measured over identical candidate lists before choosing.
- **D-005 (run 4, 2026-09-03)** — "hypothesis CONFIRMED, and D-002 resolved".
  Recall 0.24 → 0.7033, p50 latency 1097 ms → 156 ms.

The honest shape: three consecutive runs where the system **lost** to the
baseline, a root cause found by reading the retrieval code rather than tuning
weights, and one large gain afterwards. The recorded refutation is a genuine
strength of the evidence trail.

---

# SECTION 5 — LIMITATIONS AND WHAT IS NOT PROVEN

## 5.1 Claims that cannot currently be supported

1. **Attribution accuracy — at any scale.** Never measured; excluded from all
   four runs by construction (§4.1). The five-signal algorithm runs only on
   `PATCH`, not on ingestion.
2. **Conflict detection precision/recall/F1.** Never measured; the dataset has no
   labelled negative pairs. Thresholds 0.25/0.20/0.15 have **no recorded
   empirical derivation** — `DECISIONS.md` cites ADR-006 but contains no tuning
   run.
3. **The attribution weights.** No recorded derivation for
   0.35/0.30/0.15/0.10/0.10 (§1.2). Not tuned, not ablated.
4. **Comparative novelty vs. Supermemory / Mem0.** Asserted in a results-file
   note; not independently verified here.
5. **Scale.** Largest corpus measured is 300 ground-truth items / 1,311 documents
   / 4,767 memories. No claim beyond that is supported.
6. **Human-gating efficacy.** The invariant is enforced in code; **no user study
   or outcome measurement exists**. The claim is "the system cannot auto-resolve",
   not "human gating produces better decisions".
7. **Production isolation.** RLS/tenancy work is not deployed (§2.4).
8. **Ingestion completion in CI.** The hosted ingestion checks assert HTTP 202
   acceptance and a job id only; no Celery worker runs, jobs remain `pending`.
   They must not be described as proving completed ingestion.
9. **Floor-loop termination.** No proof in the repo (§1.2).

## 5.2 Status of the parallel security/role-permission work

**Committed and CI-green, but unmerged and undeployed.** Not uncertain — but not
live either.

- Branch `frontend-ci-claude`, head `135eb9b`, 7/7 workflows green, including a
  PostgreSQL 18 migration round-trip that asserts an upgrade → downgrade →
  re-upgrade with RLS flags returning to a *measured* baseline.
- Migrations `0006`, `0007`, `0008` are **not on `main`** (verified by
  `git ls-tree`), so none of it is in production.
- PR #5 is open and **unmerged**. Its only red check is GitGuardian incident
  **37270182**, which was inspected line by line and is a **false positive** —
  the flagged lines are `${VAR:?message}` shell parameter expansions containing
  no credential.
- One **confirmed, unfixed** backend defect is outstanding: concurrent removal of
  two workspace owners can leave a workspace with zero owners, because
  `sm_protect_last_workspace_owner()` performs a `NOT EXISTS` check with no
  serialisation, so under `READ COMMITTED` both transactions pass. Handed to the
  backend owner in `docs/handoff/DEFECT-concurrent-owner-removal-race.md`. **Not
  reproduced concurrently** — the reasoning is from source plus documented MVCC
  behaviour.
- A genuine historical credential appears in git history: `infra/docker-compose.security-test.yml`
  carried a literal password at `e8af933` (16 chars) and `f589079` (20 chars),
  removed at `90eb134`. The repository is public. Whether that value was reused
  as a live secret is **UNVERIFIED — needs direct confirmation by the owner**.

---

## Verification ledger

**Read in this session:** `scorer.py`, `relations.py`, `severity.py`,
`conflicts.py` (both at head and at `8072e42`), `dependencies.py`, `config.py`,
`hybrid.py`, `fact_extractor.py`, `workspace.py` (analytics), models
(`attribution`, `memory`, `memory_conflict`, `memory_relation`, `workspace`),
`20250311_0001_initial_schema.py`, the `0006`/`0007`/`0008` migrations,
`infra/docker-compose.yml`, `results_run1_prefix/2/3/4.json`, `report_run4.md`,
`sourcemind_id_map.json`, `DECISIONS.md`, and the restored-`0005` inventory.

**Not read, and therefore not asserted:** the Neo4j and Kafka propagation paths
(both feature-flagged off), the Celery worker's ingestion internals beyond the
attribution entry point, and the frontend beyond what §5.1(8) states.
