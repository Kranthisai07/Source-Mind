// Realistic mock data for SourceMind — architecture-oriented contributors,
// commit-message-style content, plausible engineering decisions.

export const WORKSPACE = {
    id: "ws_acme_platform",
    name: "Acme Platform",
    slug: "acme-platform",
    plan: "Pro",
};

export const CURRENT_USER = {
    id: "usr_01",
    login: "lina.chen",
    name: "Lina Chen",
    email: "lina@acme.dev",
    role: "Owner",
    avatarColor: "#4F7EFF",
};

export const CONTRIBUTORS = [
    { id: "c1", login: "lina.chen",     name: "Lina Chen",     count: 142, score: 0.91, last_active: "2h",  avatarColor: "#4F7EFF", top_category: "architecture" },
    { id: "c2", login: "dmitry.volk",   name: "Dmitry Volkov", count: 118, score: 0.87, last_active: "4h",  avatarColor: "#A78BFA", top_category: "incident"     },
    { id: "c3", login: "priya.nair",    name: "Priya Nair",    count:  96, score: 0.84, last_active: "1d",  avatarColor: "#34D399", top_category: "decision"     },
    { id: "c4", login: "marcus.okafor", name: "Marcus Okafor", count:  73, score: 0.79, last_active: "3h",  avatarColor: "#F59E0B", top_category: "onboarding"   },
    { id: "c5", login: "yuki.tanaka",   name: "Yuki Tanaka",   count:  58, score: 0.72, last_active: "2d",  avatarColor: "#EF4444", top_category: "architecture" },
    { id: "c6", login: "sara.hofmann",  name: "Sara Hofmann",  count:  41, score: 0.68, last_active: "5h",  avatarColor: "#60A5FA", top_category: "general"      },
    { id: "c7", login: "raj.patel",     name: "Raj Patel",     count:  33, score: 0.64, last_active: "12d", avatarColor: "#F472B6", top_category: "incident"     },
    { id: "c8", login: "noah.rivera",   name: "Noah Rivera",   count:  22, score: 0.58, last_active: "1h",  avatarColor: "#2DD4BF", top_category: "decision"     },
];

const byLogin = (login) => CONTRIBUTORS.find(c => c.login === login) || CONTRIBUTORS[0];

const MEMORY_SEEDS = [
    {
        content: `Adopted **Postgres row-level security (RLS)** for multi-tenant isolation instead of schema-per-tenant.

### Why
Trade-off accepted: slightly higher query planning cost vs. *massively simpler ops* + connection pooling.

### Rules
- All new tables **MUST** declare a policy on \`workspace_id\`.
- Every query **MUST** set \`SET LOCAL app.workspace_id = '...'\` before running.
- Superuser bypass is disabled in staging + prod.

### Example policy

\`\`\`sql
CREATE POLICY tenant_isolation ON memories
    USING (workspace_id = current_setting('app.workspace_id')::uuid);
\`\`\`

> Revisit if we ever onboard a customer with >50M rows per table — at that point
> schema-per-tenant starts winning on planner cost.`,
        tags: ["postgres", "security", "multi-tenant"],
        category: "decision",
        primary: "lina.chen",
        co: [["dmitry.volk", 0.22], ["priya.nair", 0.15]],
    },
    {
        content: `## Incident 2026-01-14 — Redis OOM

**Impact:** 14 min degraded search, no data loss.

### Timeline
1. \`14:02\` — p99 search latency spikes from 40ms → 3.8s
2. \`14:06\` — on-call paged, Redis memory at 97%
3. \`14:09\` — OOM kill, auto-restart
4. \`14:16\` — recovery confirmed

### Root cause
Unbounded \`search:*\` keys from the hybrid search cache.

### Mitigation
- Added \`EXPIRE 3600\` on all search keys
- Key-count alerting at >500k
- Follow-up ticket [ENG-4821](https://linear.app/acme/eng-4821) filed for LFU eviction`,
        tags: ["incident", "redis", "postmortem"],
        category: "incident",
        primary: "dmitry.volk",
        co: [["marcus.okafor", 0.30], ["lina.chen", 0.10]],
    },
    {
        content: `## pgvector HNSW tuning

Sweet spot for our **2.4M embedding corpus**:

| Param | Value | Notes |
|---|---|---|
| \`m\` | 16 | higher = better recall, more memory |
| \`ef_construction\` | 64 | build time |
| \`ef_search\` | 40 | query time |

### Results
- Recall@10 ≈ **0.94**
- p95 query ≈ **28ms**

> Revisit when corpus exceeds 10M. At that scale HNSW memory starts competing with the page cache.`,
        tags: ["pgvector", "search", "performance"],
        category: "architecture",
        primary: "priya.nair",
        co: [["lina.chen", 0.25]],
    },
    {
        content: "Onboarding: clone the monorepo, run `pnpm install`, then `docker compose up db redis minio`. Do NOT run the full stack locally — use `--filter` to boot only the apps you're touching. Full-stack-local takes ~14 min to start and burns laptop battery.",
        tags: ["onboarding", "dx", "monorepo"],
        category: "onboarding",
        primary: "marcus.okafor",
        co: [["sara.hofmann", 0.18]],
    },
    {
        content: "BM25 + dense retrieval fusion via reciprocal rank fusion (k=60). We considered learned-rerank with a cross-encoder but latency budget is 50ms p95 — can't afford the extra 80ms. Revisit when we have GPU inference capacity.",
        tags: ["search", "ranking", "ml"],
        category: "decision",
        primary: "lina.chen",
        co: [["priya.nair", 0.34], ["yuki.tanaka", 0.12]],
    },
    {
        content: "Discord connector uses JSON export (not gateway) to stay compliant with Discord's ToS. Users upload a zip from DiscordChatExporter; we parse and attribute messages to the original author via their Discord handle → team member mapping table.",
        tags: ["discord", "connector", "compliance"],
        category: "architecture",
        primary: "yuki.tanaka",
        co: [["marcus.okafor", 0.20]],
    },
    {
        content: "All ingest jobs flow through a 7-stage pipeline: receive → extract → chunk → embed → store → attribute → done. Each stage emits a Celery task with an idempotency key. Failed stages retry with exponential backoff (max 5 attempts). Partial success is persisted.",
        tags: ["ingest", "celery", "pipeline"],
        category: "architecture",
        primary: "dmitry.volk",
        co: [["lina.chen", 0.20], ["noah.rivera", 0.10]],
    },
    {
        content: "We do NOT store raw GitHub tokens. Each workspace gets an installed GitHub App; we exchange the installation ID for a short-lived token on every sync. Zero long-lived credentials at rest.",
        tags: ["github", "security", "auth"],
        category: "decision",
        primary: "sara.hofmann",
        co: [["lina.chen", 0.25]],
    },
    {
        content: "Knowledge Health Score = 0.30·Coverage + 0.30·Freshness + 0.25·(1 − ConflictRatio) + 0.15·Attribution. Designed so that an active team with zero conflicts and good attribution lands around 85–92.",
        tags: ["analytics", "scoring"],
        category: "decision",
        primary: "priya.nair",
        co: [["lina.chen", 0.20]],
    },
    {
        content: "Conflict detection runs on every memory insert: we compute the top-3 nearest neighbors via pgvector and call Claude to judge contradiction vs. complement. Only contradictions produce a conflict record.",
        tags: ["conflict", "claude", "ml"],
        category: "architecture",
        primary: "lina.chen",
        co: [["priya.nair", 0.30]],
    },
    {
        content: "Attribution signals (S1–S5): S1 original author, S2 substantial edit, S3 minor edit, S4 review/approval, S5 citation. Weights currently 0.45 / 0.25 / 0.10 / 0.12 / 0.08. Sum to 1.0. Tuned on internal labelled set (n=2,400).",
        tags: ["attribution", "scoring"],
        category: "decision",
        primary: "priya.nair",
        co: [["dmitry.volk", 0.18]],
    },
    {
        content: "Handoff tiers: CRITICAL ≥ solo-author on ≥5 memories or ≥80% attribution on any CRITICAL-tagged memory. IMPORTANT ≥ top-3 contributor on ≥10 memories. STANDARD = everything else. Departing member must transfer CRITICAL tier before their last day.",
        tags: ["handoff", "policy"],
        category: "decision",
        primary: "lina.chen",
        co: [["sara.hofmann", 0.15]],
    },
    {
        content: "API rate limits enforced via Upstash Redis sliding window. Free tier: 100 req/min, Pro: 1000, Enterprise: 10000. `X-RateLimit-Remaining` and `X-RateLimit-Reset` headers returned on every response.",
        tags: ["api", "rate-limit", "upstash"],
        category: "architecture",
        primary: "noah.rivera",
        co: [["dmitry.volk", 0.22]],
    },
    {
        content: "Incident 2025-12-03: Clerk webhook signature verification was checking the wrong header (`Svix-Signature` vs `svix-signature`). Silent failure for 18 hours — user.created events weren't syncing to our DB. Added integration test + alerting on webhook failure rate > 1%.",
        tags: ["incident", "clerk", "webhook"],
        category: "incident",
        primary: "marcus.okafor",
        co: [["sara.hofmann", 0.20]],
    },
    {
        content: "We ship every Thursday. No deploys on Friday unless there's an active P0 incident. Rollbacks are one-click via Railway. Anyone on the team can trigger a rollback — no approval needed.",
        tags: ["deploy", "culture"],
        category: "general",
        primary: "lina.chen",
        co: [["dmitry.volk", 0.18]],
    },
    {
        content: "Claude Sonnet 4.6 is our primary extraction model. Claude Haiku is used for low-stakes summarization (recent activity tooltips, etc.). Never call Opus in prod — cost is 5x Sonnet for marginal quality gains on our tasks.",
        tags: ["claude", "cost", "ml"],
        category: "decision",
        primary: "priya.nair",
        co: [["lina.chen", 0.25]],
    },
];

const TIME_OFFSETS_HOURS = [1, 3, 5, 8, 14, 22, 30, 48, 72, 96, 120, 168, 240, 336, 480, 720, 960];

function buildAttribution(primaryLogin, coList) {
    const primary = byLogin(primaryLogin);
    const coTotal = coList.reduce((a, [, s]) => a + s, 0);
    const primaryScore = Math.max(0.4, 1 - coTotal);
    return [
        { author: primary.login, name: primary.name, score: +primaryScore.toFixed(2), color: primary.avatarColor, signals: { S1: 0.45, S2: 0.25, S3: 0.10, S4: 0.12, S5: 0.08 } },
        ...coList.map(([login, s]) => {
            const c = byLogin(login);
            return { author: c.login, name: c.name, score: +s.toFixed(2), color: c.avatarColor, signals: { S1: 0.10, S2: 0.40, S3: 0.20, S4: 0.18, S5: 0.12 } };
        }),
    ];
}

export const MEMORIES = MEMORY_SEEDS.map((seed, i) => {
    const hoursAgo = TIME_OFFSETS_HOURS[i % TIME_OFFSETS_HOURS.length];
    const created = new Date(Date.now() - hoursAgo * 3600 * 1000);
    return {
        memory_id: `mem_${String(1001 + i)}`,
        content: seed.content,
        tags: seed.tags,
        category: seed.category,
        workspace_id: WORKSPACE.id,
        created_at: created.toISOString(),
        is_latest: true,
        attribution: buildAttribution(seed.primary, seed.co || []),
        version: 1 + (i % 4),
        versions: [
            { v: 1, editor: seed.primary, at: new Date(created.getTime() - 5 * 86400000).toISOString(), summary: "initial creation" },
            { v: 2, editor: (seed.co[0] && seed.co[0][0]) || seed.primary, at: new Date(created.getTime() - 3 * 86400000).toISOString(), summary: "+ 4 lines, - 1 line" },
            { v: 3, editor: seed.primary, at: new Date(created.getTime() - 86400000).toISOString(), summary: "+ 2 lines" },
        ].slice(0, 1 + (i % 3)),
    };
});

export const CONFLICTS = [
    {
        id: "cfl_501",
        status: "open",
        severity: "high",
        detected_at: new Date(Date.now() - 6 * 3600 * 1000).toISOString(),
        memory_a_excerpt: "pgvector HNSW index tuning: m=16, ef_construction=64, ef_search=40 is the sweet spot for our 2.4M embedding corpus.",
        memory_b_excerpt: "For pgvector at our scale, use IVFFlat with lists=100. HNSW has too high memory overhead for nodes with <32GB RAM.",
        conflict_type: "contradiction · configuration",
        contributors: ["priya.nair", "yuki.tanaka"],
        ai_suggestion: "These claims conflict on index choice. The HNSW claim (Memory A) is more recent and aligns with pgvector 0.7+ best practices at this corpus size. IVFFlat (Memory B) was valid for older deployments pre-2024. Recommend: accept Memory A as canonical, mark Memory B as outdated with a link to the newer doc.",
    },
    {
        id: "cfl_502",
        status: "under_review",
        severity: "medium",
        detected_at: new Date(Date.now() - 22 * 3600 * 1000).toISOString(),
        memory_a_excerpt: "All ingest jobs flow through a 7-stage pipeline: receive → extract → chunk → embed → store → attribute → done.",
        memory_b_excerpt: "Ingest is a 5-stage process: receive, chunk, embed, store, attribute. Extraction and completion are implicit.",
        conflict_type: "naming / count discrepancy",
        contributors: ["dmitry.volk", "noah.rivera"],
        ai_suggestion: "This is a naming disagreement, not a true contradiction. Memory A's 7-stage model is the canonical one used in the observability dashboard. Recommend: merge — update Memory B to reference the 7-stage model while preserving the simpler mental model as a 'summary view'.",
    },
    {
        id: "cfl_503",
        status: "open",
        severity: "low",
        detected_at: new Date(Date.now() - 3 * 86400 * 1000).toISOString(),
        memory_a_excerpt: "Claude Sonnet 4.6 is our primary extraction model. Never call Opus in prod.",
        memory_b_excerpt: "Extraction model has been pinned to Claude Opus for complex architectural documents. Sonnet loses nuance on nested design rationale.",
        conflict_type: "policy vs. exception",
        contributors: ["priya.nair", "raj.patel"],
        ai_suggestion: "Memory B describes a real exception case (nested architectural docs). Recommend: merge — keep Sonnet as default, add explicit carve-out for docs tagged 'adr' or 'architecture-deep-dive'.",
    },
    {
        id: "cfl_504",
        status: "resolved",
        severity: "medium",
        detected_at: new Date(Date.now() - 8 * 86400 * 1000).toISOString(),
        memory_a_excerpt: "We ship every Thursday. No deploys on Friday.",
        memory_b_excerpt: "Deploy freeze window: Friday 12:00 UTC through Monday 08:00 UTC.",
        conflict_type: "policy consolidation",
        contributors: ["lina.chen", "dmitry.volk"],
        ai_suggestion: "Memory B is more precise and does not contradict Memory A. Recommend: accept Memory B as the authoritative statement.",
    },
    {
        id: "cfl_505",
        status: "deferred",
        severity: "low",
        detected_at: new Date(Date.now() - 12 * 86400 * 1000).toISOString(),
        memory_a_excerpt: "Free tier: 100 req/min.",
        memory_b_excerpt: "Free plan users get 60 req/min after the 2025 pricing update.",
        conflict_type: "stale content",
        contributors: ["noah.rivera", "sara.hofmann"],
        ai_suggestion: "Memory B reflects the current pricing page. Memory A appears to be pre-update. Recommend: mark Memory A as outdated.",
    },
];

export const KNOWLEDGE_GAPS = [
    { risk_level: "HIGH",   gap_type: "solo_author",    description: "5 CRITICAL-tier memories are solo-authored by lina.chen with no co-contributors. Single point of failure.", affected_count: 5, recommended_action: "Initiate peer review with priya.nair or dmitry.volk." },
    { risk_level: "HIGH",   gap_type: "departing_owner", description: "raj.patel owns 12% of the incident-response knowledge. Departure confirmed for 2026-03-14.", affected_count: 23, recommended_action: "Start a handoff to marcus.okafor immediately." },
    { risk_level: "MEDIUM", gap_type: "stale_content",   description: "14 memories haven't been touched in > 180 days but are still marked 'latest'. Likely drift risk.", affected_count: 14, recommended_action: "Flag for freshness review this sprint." },
    { risk_level: "MEDIUM", gap_type: "low_coverage",    description: "Only 2 memories tagged 'billing' — unusual for a platform at this ARR stage.", affected_count: 2, recommended_action: "Schedule a 30-min capture session with the payments team." },
    { risk_level: "LOW",    gap_type: "thin_attribution", description: "18 memories have only a single attribution signal. Attribution reliability below 0.5.", affected_count: 18, recommended_action: "Add review/citation signals via PR links." },
    { risk_level: "LOW",    gap_type: "no_tags",         description: "7 memories have zero tags. Reduces discoverability.", affected_count: 7, recommended_action: "Run auto-tag job across corpus." },
];

export const CONNECTORS = [
    { id: "conn_01", source_tool: "github",  name: "acme/platform",         config: { repo: "acme/platform",   artifact_types: ["commits", "pulls", "issues", "discussions"], frequency: "hourly" }, status: "active",  last_synced_at: new Date(Date.now() - 12 * 60 * 1000).toISOString(), total_artifacts_synced: 12481 },
    { id: "conn_02", source_tool: "github",  name: "acme/infra",            config: { repo: "acme/infra",      artifact_types: ["commits", "pulls"],                         frequency: "daily"  }, status: "active",  last_synced_at: new Date(Date.now() -  3 * 3600 * 1000).toISOString(), total_artifacts_synced: 3102  },
    { id: "conn_03", source_tool: "github",  name: "acme/design-system",    config: { repo: "acme/design-system", artifact_types: ["commits", "pulls", "issues"],             frequency: "daily"  }, status: "syncing", last_synced_at: new Date(Date.now() -  1 * 60   * 1000).toISOString(), total_artifacts_synced: 892   },
    { id: "conn_04", source_tool: "discord", name: "Acme Eng — #architecture", config: { channel: "#architecture", source: "json-export" },                                        status: "active",  last_synced_at: new Date(Date.now() -  4 * 3600 * 1000).toISOString(), total_artifacts_synced: 2341  },
    { id: "conn_05", source_tool: "discord", name: "Acme Eng — #incidents",    config: { channel: "#incidents",    source: "json-export" },                                        status: "error",   last_synced_at: new Date(Date.now() - 28 * 3600 * 1000).toISOString(), total_artifacts_synced: 402,   error: "Malformed export: missing author.id on 14 messages" },
    { id: "conn_06", source_tool: "github",  name: "acme/mobile",           config: { repo: "acme/mobile",     artifact_types: ["commits", "pulls"],                         frequency: "hourly" }, status: "active",  last_synced_at: new Date(Date.now() - 30 * 60   * 1000).toISOString(), total_artifacts_synced: 5211  },
];

export const SYNC_LOGS = [
    { date: new Date(Date.now() - 12 * 60 * 1000).toISOString(),     status: "success", items_synced: 42, duration_ms: 3820, error: null },
    { date: new Date(Date.now() - 72 * 60 * 1000).toISOString(),     status: "success", items_synced: 37, duration_ms: 3410, error: null },
    { date: new Date(Date.now() - 130 * 60 * 1000).toISOString(),    status: "partial", items_synced: 29, duration_ms: 5102, error: "3 items skipped: rate limit" },
    { date: new Date(Date.now() - 190 * 60 * 1000).toISOString(),    status: "success", items_synced: 51, duration_ms: 4018, error: null },
    { date: new Date(Date.now() - 250 * 60 * 1000).toISOString(),    status: "failed",  items_synced: 0,  duration_ms: 820,  error: "401 Bad credentials — installation token expired" },
    { date: new Date(Date.now() - 310 * 60 * 1000).toISOString(),    status: "success", items_synced: 44, duration_ms: 3702, error: null },
];

export const HANDOFFS = [
    {
        id: "hnd_01",
        departing_user_id: "c7",
        departing_user_name: "Raj Patel",
        receiving_user_id: "c4",
        receiving_user_name: "Marcus Okafor",
        tier: "CRITICAL",
        total_memories: 23,
        status: "assigned",
        created_at: new Date(Date.now() - 2 * 86400 * 1000).toISOString(),
    },
    {
        id: "hnd_02",
        departing_user_id: "c6",
        departing_user_name: "Sara Hofmann",
        receiving_user_id: "c8",
        receiving_user_name: null,        // freshly initiated — not yet assigned
        tier: "IMPORTANT",
        total_memories: 14,
        status: "initiated",
        created_at: new Date(Date.now() - 1 * 86400 * 1000).toISOString(),
    },
    {
        id: "hnd_03",
        departing_user_id: "c5",
        departing_user_name: "Yuki Tanaka",
        receiving_user_id: "c2",
        receiving_user_name: "Dmitry Volkov",
        tier: "STANDARD",
        total_memories: 7,
        status: "initiated",
        created_at: new Date(Date.now() - 4 * 3600 * 1000).toISOString(),
    },
];

export const RECENT_ACTIVITY = [
    { type: "memory_created",  color: "#4F7EFF", description: "lina.chen ingested 'Postgres RLS for multi-tenant isolation'",          ts_min_ago: 2   },
    { type: "conflict_opened", color: "#F59E0B", description: "Conflict detected: pgvector HNSW vs. IVFFlat indexing",                  ts_min_ago: 14  },
    { type: "connector_synced",color: "#34D399", description: "acme/platform synced · 42 new commits, 3 PRs",                           ts_min_ago: 18  },
    { type: "handoff_started", color: "#A78BFA", description: "Handoff initiated: raj.patel → marcus.okafor (CRITICAL, 23 memories)",   ts_min_ago: 52  },
    { type: "memory_edited",   color: "#4F7EFF", description: "priya.nair edited 'Attribution signal weights'",                         ts_min_ago: 78  },
    { type: "conflict_resolved",color:"#34D399", description: "dmitry.volk resolved CFL-504 (deploy freeze window)",                    ts_min_ago: 120 },
    { type: "memory_created",  color: "#4F7EFF", description: "dmitry.volk ingested 'Incident 2026-01-14 postmortem'",                  ts_min_ago: 180 },
    { type: "connector_synced",color: "#34D399", description: "Acme Eng — #architecture synced · 89 messages",                          ts_min_ago: 240 },
];

// 30-day memory creation series + search activity
export const MEMORIES_OVER_TIME = Array.from({ length: 30 }, (_, i) => {
    const day = new Date(Date.now() - (29 - i) * 86400 * 1000);
    const base = 4 + Math.sin(i / 3.2) * 3;
    const noise = (i * 7 + 11) % 5;
    return { day: day.toISOString().slice(5, 10), count: Math.max(1, Math.round(base + noise)) };
});

export const SEARCH_ACTIVITY = Array.from({ length: 14 }, (_, i) => ({
    day: i,
    searches: Math.round(50 + Math.sin(i / 2) * 20 + (i * 13 % 9)),
}));

export const TEAM_MEMBERS = CONTRIBUTORS.slice(0, 6).map((c, i) => ({
    ...c,
    email: `${c.login}@acme.dev`,
    role: i === 0 ? "Owner" : i < 3 ? "Admin" : "Member",
}));
