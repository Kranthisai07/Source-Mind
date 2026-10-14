// Mock API client that matches the exact SourceMind backend contract shapes.
// Swap the `request()` helper with real fetch calls to /v1/... endpoints later —
// UI components will not need any changes.

import {
    WORKSPACE, CURRENT_USER, CONTRIBUTORS, MEMORIES, CONFLICTS,
    KNOWLEDGE_GAPS, CONNECTORS, SYNC_LOGS, HANDOFFS, RECENT_ACTIVITY,
    MEMORIES_OVER_TIME, SEARCH_ACTIVITY, TEAM_MEMBERS,
    CONNECTOR_STATUSES, MEMORY_SOURCES,
} from "./mockData";

// ----- helpers -----
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const latency = (min = 180, max = 420) => Math.floor(min + Math.random() * (max - min));

async function request(fn, { min = 180, max = 420 } = {}) {
    const ms = latency(min, max);
    await sleep(ms);
    const data = typeof fn === "function" ? fn() : fn;
    return { ...data, __latency_ms: ms };
}

function conflictSummary(conflict) {
    return {
        id: conflict.id,
        status: conflict.status,
        conflict_type: conflict.conflict_type,
        severity: conflict.severity === "high" ? "critical" : conflict.severity,
        similarity_score: conflict.similarity_score ?? null,
        explanation: conflict.explanation ?? null,
        memory_a_id: conflict.memory_a_id ?? `${conflict.id}-memory-a`,
        memory_a_content: conflict.memory_a_content ?? conflict.memory_a_excerpt,
        memory_b_id: conflict.memory_b_id ?? `${conflict.id}-memory-b`,
        memory_b_content: conflict.memory_b_content ?? conflict.memory_b_excerpt,
        created_at: conflict.created_at ?? conflict.detected_at,
    };
}

function conflictDetail(conflict) {
    const summary = conflictSummary(conflict);
    return {
        id: summary.id,
        status: summary.status,
        conflict_type: summary.conflict_type,
        severity: summary.severity,
        similarity_score: summary.similarity_score,
        explanation: summary.explanation,
        memory_a: { id: summary.memory_a_id, content: summary.memory_a_content },
        memory_b: { id: summary.memory_b_id, content: summary.memory_b_content },
        reviewed_by: null,
        reviewed_at: null,
        revisit_at: null,
        created_at: summary.created_at,
    };
}

// Source provenance fields exist only on the first demo memories, like the
// real API where only imported memories have a source.
function withSource(memory) {
    const index = MEMORIES.findIndex((m) => m.memory_id === memory.memory_id);
    const extra = index >= 0 ? MEMORY_SOURCES[index] : null;
    return extra ? { ...memory, ...extra } : memory;
}

// Connector state lives for the page session.
const mockConnectors = CONNECTORS.map((c) => ({ ...c }));

// The demo has one workspace; ones created in the UI live for the page session.
const mockWorkspaces = [WORKSPACE];
// Set by realApi.selectWorkspace so mock reads follow the picker.
let mockSelectedId = null;

// ----- workspace -----
export const mockApi = {
    getCurrentUser: () => request(() => CURRENT_USER),
    getWorkspace:   () => request(() => mockWorkspaces.find((w) => w.id === mockSelectedId) || WORKSPACE),
    selectWorkspace: (id) => { mockSelectedId = id; },
    listWorkspaces: () => request(() => ({ items: [...mockWorkspaces] })),
    createWorkspace: async ({ name, slug, description } = {}) =>
        request(() => {
            if (mockWorkspaces.some((w) => w.slug === slug)) {
                const err = new Error("409 Conflict");
                err.status = 409;
                err.body = { error: { code: "SM031", message: "Workspace slug already exists" } };
                throw err;
            }
            const created = { id: `ws_${slug}`, name, slug, description: description || null, plan: "Free" };
            mockWorkspaces.push(created);
            return created;
        }),
    addWorkspaceMember: async (_workspaceId, { email, role = "member" } = {}) =>
        request(() => ({
            user: { id: `usr_${String(email).split("@")[0]}`, display_name: String(email).split("@")[0], avatar_url: null },
            role,
            joined_at: new Date().toISOString(),
        })),

    // GET /v1/workspaces/:id/analytics/overview
    getAnalyticsOverview: (_wsId) => request(() => ({
        total_memories: MEMORIES.length + 218,
        total_contributors: CONTRIBUTORS.length,
        memories_created_last_30_days: 47,
        open_conflicts: CONFLICTS.filter(c => c.status === "open").length,
        knowledge_health_score: 82,
        health_breakdown: {
            coverage: 78,
            freshness: 71,
            conflict_ratio: 88,
            attribution_coverage: 91,
        },
        top_contributors: CONTRIBUTORS.slice(0, 6).map(c => ({
            name: c.login, count: c.count, color: c.avatarColor,
        })),
        recent_activity: RECENT_ACTIVITY.map(a => ({
            type: a.type,
            description: a.description,
            color: a.color,
            timestamp: new Date(Date.now() - a.ts_min_ago * 60 * 1000).toISOString(),
        })),
    })),

    // POST /v1/memories/search
    searchMemories: ({ query = "", mode = "hybrid", limit = 20, include_attribution = false, include_source = false } = {}) =>
        request(() => {
            const q = query.trim().toLowerCase();
            let results = MEMORIES;
            if (q) {
                // Tokenize — match if any word of length >=3 appears anywhere.
                const tokens = q.split(/\s+/).filter(w => w.length >= 3);
                results = MEMORIES.filter(m => {
                    const haystack = (
                        m.content + " " + m.tags.join(" ") + " " + m.category
                    ).toLowerCase();
                    return tokens.length === 0
                        ? haystack.includes(q)
                        : tokens.some(t => haystack.includes(t));
                });
            }
            return {
                // Like the API: attribution only when asked for.
                results: results.slice(0, limit).map((m) => {
                    // Like the API: source fields only when asked for.
                    const sourced = include_source ? withSource(m) : m;
                    if (include_attribution) return sourced;
                    const { attribution: _omitted, ...withoutAttribution } = sourced;
                    return withoutAttribution;
                }),
                total_found: results.length,
                mode,
            };
        }, { min: 40, max: 90 }),

    // GET /v1/memories/:id
    getMemory: (id) => request(() => {
        const m = MEMORIES.find(x => x.memory_id === id);
        // is_current / latest_memory_id / supersession_reason mirror the
        // superseded-versions contract; an in-memory mock edit is always current.
        return m
            ? { is_current: true, latest_memory_id: null, supersession_reason: null, ...withSource(m) }
            : { error: "not_found" };
    }),

    // GET /v1/memories/:id/versions — mirrors MemoryVersionsResponse so mock
    // mode exercises the same shape the real endpoint returns.
    getMemoryVersions: (id) => request(() => {
        const m = MEMORIES.find(x => x.memory_id === id);
        const versions = (m?.versions ?? []).map((v, i) => ({
            id: `${id}-v${v.v ?? i + 1}`,
            version: v.v ?? i + 1,
            is_current: i === 0,
            content: v.summary ?? "",
            created_at: v.at ?? null,
        }));
        return { versions, total: versions.length };
    }),

    // POST /v1/memories (returns job_id). Like the real API: the text is
    // trimmed, and identical text returns the ORIGINAL job with
    // `already_exists: true` instead of creating anything.
    _jobs: {},
    createMemory(payload = {}) {
        const content = String(payload.content ?? "").trim();
        const existing = Object.entries(this._jobs).find(([, job]) => job.content === content);
        let jobId;
        if (existing) {
            jobId = existing[0];
        } else {
            jobId = `job_${Date.now().toString(36)}_${Object.keys(this._jobs).length}`;
            this._jobs[jobId] = {
                content,
                tags: Array.isArray(payload.tags) ? payload.tags : [],
                category: payload.category || "general",
                memoryId: null,
            };
        }
        return request(() => ({
            job_id: jobId,
            status: existing ? "completed" : "queued",
            message: existing ? "Document already ingested." : "Document is queued and will be processed shortly.",
            memory_count: existing ? 1 : null,
            already_exists: Boolean(existing),
        }), { min: 120, max: 240 });
    },

    updateMemory: (id, payload) => request(() => {
        const memory = MEMORIES.find((item) => item.memory_id === id || item.id === id);
        if (!memory) return { error: "not_found" };
        memory.content = payload.content;
        if (Array.isArray(payload.tags)) memory.tags = payload.tags;
        memory.version = (memory.version || 1) + 1;
        memory.updated_at = new Date().toISOString();
        return memory;
    }),

    createPilotRating: (payload) => request(() => ({
        id: `rating_${Date.now().toString(36)}`,
        ...payload,
        created_at: new Date().toISOString(),
    })),

    getPilotExport: () => request(() => ({
        schema_version: 2,
        canonical_sha256: "mock-pilot-export",
        datasets: {},
    })),

    // GET /v1/memories/jobs/:id — mirrors IngestionJobResponse: a `status`
    // that advances through the worker's stages, then `done` (realApi rewrites
    // `completed` to `done`), with `memory_ids` / `memory_count`. Like the real
    // API there is no stage list and no elapsed time.
    _jobStart: {},
    getJobStatus(jobId) {
        if (!this._jobStart[jobId]) this._jobStart[jobId] = Date.now();
        const elapsed = Date.now() - this._jobStart[jobId];
        const steps = [
            [300, "extracting"], [700, "chunking"], [1200, "extracting_facts"],
            [1700, "embedding"], [2100, "indexing"],
        ];
        const step = steps.find(([until]) => elapsed < until);
        const done = !step;
        // The memory the "pipeline" produced is the submitted text itself, so
        // the success link opens exactly what was ingested and search finds it.
        const submitted = this._jobs[jobId];
        if (done && submitted && !submitted.memoryId) {
            submitted.memoryId = `mem_new_${jobId}`;
            MEMORIES.unshift({
                memory_id: submitted.memoryId,
                content: submitted.content,
                tags: submitted.tags,
                category: submitted.category,
                workspace_id: WORKSPACE.id,
                created_at: new Date().toISOString(),
                is_latest: true,
                attribution: [],
                version: 1,
                versions: [],
            });
        }
        const memoryIds = done && submitted?.memoryId ? [submitted.memoryId] : [];
        return request(() => ({
            job_id: jobId,
            status: done ? "done" : step[1],
            message: done ? "Ingestion complete." : null,
            memory_ids: memoryIds,
            memory_count: memoryIds.length,
            chunk_count: done ? 1 : 0,
            error: null,
        }), { min: 60, max: 120 });
    },

    // GET /v1/workspaces/:id/conflicts
    listConflicts: (_wsId, { status = "all" } = {}) => request(() => {
        const conflicts = CONFLICTS
            .filter((conflict) => !status || status === "all" || conflict.status === status)
            .map(conflictSummary);
        return { conflicts, total: conflicts.length, next_cursor: null };
    }),

    // GET /v1/conflicts/:id
    getConflict: (id) => request(() => {
        const c = CONFLICTS.find(x => x.id === id);
        return c ? conflictDetail(c) : { error: "not_found" };
    }),

    // POST /v1/conflicts/:id/resolve
    resolveConflict: (_id, {
        resolution_type,
        note,
        merged_content,
        tag_a,
        tag_b,
        revisit_at,
    }) => request(() => ({
        status: "ok",
        resolution_type,
        note,
        merged_content,
        tag_a,
        tag_b,
        revisit_at,
    }), { min: 250, max: 500 }),

    // GET /v1/workspaces/:id/connectors -> ConnectorListResponse {items, total}
    // (realApi also exposes `connectors`, the same list).
    listConnectors: (_wsId) => request(() => ({
        items: mockConnectors,
        connectors: mockConnectors,
        total: mockConnectors.length,
    })),

    // GET /v1/connectors/:id/status -> ConnectorStatusResponse
    getConnectorStatus: (id) => request(() => {
        const status = CONNECTOR_STATUSES[id];
        if (!status) {
            const err = new Error("404 Not Found");
            err.status = 404;
            err.body = { error: { code: "SM027", message: "Connector not found" } };
            throw err;
        }
        return status;
    }, { min: 60, max: 140 }),

    // POST /v1/connectors/:id/sync -> 202 SyncTriggerResponse
    triggerSync: (connId, { sync_type = "incremental" } = {}) => request(() => ({
        task_id: `task_${Date.now().toString(36)}`,
        connector_id: connId,
        sync_type,
        message: `Sync task enqueued (type=${sync_type})`,
    }), { min: 300, max: 600 }),

    // POST /v1/connectors/:id/retry-failed -> RetryFailedResponse
    retryFailedConnector: (connId) => request(() => ({
        connector_id: connId, eligible: 1, requeued: 1, refused: 0, remaining: 0,
    })),

    // POST /v1/connectors/:id/rescan -> 202 RescanResponse
    rescanConnector: (connId) => request(() => ({
        connector_id: connId,
        scopes_marked: (CONNECTOR_STATUSES[connId]?.scopes || []).length,
        message: "Scopes marked for a full inventory pass; the next sync runs it.",
    })),

    // PATCH /v1/connectors/:id -> ConnectorResponse
    updateConnector: (id, payload = {}) => request(() => {
        const c = mockConnectors.find((x) => x.id === id);
        if (c && payload.status) c.status = payload.status;
        if (c && payload.display_name) c.display_name = payload.display_name;
        return c || {};
    }),

    // DELETE /v1/connectors/:id?remove_memories= -> 200 DisconnectResponse
    deleteConnector: (id, { remove_memories = false } = {}) => request(() => {
        const c = mockConnectors.find((x) => x.id === id);
        if (c) c.status = "disconnected";
        return {
            connector_id: id, status: "disconnected", generation: 2, remove_memories,
            documents_considered: 12, memories_retired: remove_memories ? 11 : 0, memories_flagged: remove_memories ? 1 : 0,
        };
    }),

    // GET /v1/connectors/:id/sync-logs -> SyncLogsResponse {items, total}
    getSyncLogs: (_connId) => request(() => ({ items: SYNC_LOGS, total: SYNC_LOGS.length })),

    // GET /v1/workspaces/:id/connectors/github/authorize -> GitHubAuthorizeResponse.
    // The mock "GitHub" is this same app's callback route.
    authorizeGithub: (_wsId) => request(() => ({
        authorize_url: `${window.location.origin}${process.env.PUBLIC_URL || ""}/connectors/github/callback?code=mock-code&state=mock-state`,
        state: "mock-state",
        expires_in_seconds: 600,
    })),

    // POST /v1/connectors/github/callback -> GitHubCallbackResponse
    completeGithubCallback: () => request(() => ({
        ticket_id: "mock-ticket-0123456789abcdef",
        expires_in_seconds: 900,
        workspace_id: WORKSPACE.id,
        installation_id: 4242,
        github_user_id: 1001,
        github_login: "acme-admin",
        repositories: [
            { full_name: "acme/platform", private: true, default_branch: "main" },
            { full_name: "acme/mobile", private: true, default_branch: "main" },
            { full_name: "acme/docs", private: false, default_branch: "main" },
        ],
        truncated: false,
    })),

    // POST /v1/workspaces/:id/connectors -> 201 ConnectorResponse
    createConnector: (_wsId, payload = {}) => request(() => {
        const created = {
            id: `00000000-0000-4000-8000-${String(mockConnectors.length + 1).padStart(12, "0")}`,
            workspace_id: WORKSPACE.id, connector_type: "github",
            display_name: payload.display_name,
            config: payload.config,
            status: "active", last_sync_at: null, next_sync_at: null,
            created_at: new Date().toISOString(), installation_id: 4242, authorized_github_user_id: 1001,
            created_by: null, generation: 1, last_success_at: null, last_error_code: null, sync_interval_minutes: 60,
        };
        mockConnectors.push(created);
        CONNECTOR_STATUSES[created.id] = {
            connector_id: created.id, workspace_id: WORKSPACE.id, connector_type: "github",
            display_name: created.display_name, status: "active", state: "idle", generation: 1,
            last_sync_at: null, next_sync_at: null, last_success_at: null, last_error_code: null,
            current_run: null, scopes: [], documents: { pending: 0, processing: 0, completed: 0, failed: 0, quarantined: 0, zero_facts: 0 },
            filtered: {}, recent_logs: [],
        };
        return created;
    }, { min: 300, max: 600 }),

    // POST /v1/memories/:id/reapply -> MemoryResponse
    reapplyMemory: (_retiredId, { target_memory_id, content } = {}) => request(() => ({
        id: target_memory_id, content, version: 2, tags: [], category: null,
        created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    })),

    // GET /v1/workspaces/:id/analytics/knowledge-gaps
    getKnowledgeGaps: (_wsId) => request(() => ({ gaps: KNOWLEDGE_GAPS })),

    // GET /v1/workspaces/:id/handoffs  (new endpoint being added backend-side)
    listHandoffs: (_wsId) => request(() => ({ handoffs: HANDOFFS })),

    // POST /v1/team/workspaces/{id}/handoff/initiate
    classifyHandoff: ({ departing_id }) => request(() => {
        const c = CONTRIBUTORS.find(x => x.id === departing_id) || CONTRIBUTORS[0];
        // Pull memories this person authored as primary.
        const authored = MEMORIES.filter(m => m.attribution[0]?.author === c.login);
        const others = CONTRIBUTORS.filter(x => x.id !== c.id);
        const tier_1_critical = authored.slice(0, 3).map((m, i) => {
            const successor = others[i % others.length];
            return {
                memory_id: m.memory_id,
                content: m.content,
                importance_score: +(0.92 - i * 0.06).toFixed(2),
                suggested_successor_id: successor.id,
                suggested_successor_name: successor.name,
                successor_confidence: +(0.87 - i * 0.08).toFixed(2),
            };
        });
        const tier_2_important = authored.slice(3, 7).map((m, i) => ({
            memory_id: m.memory_id,
            content: m.content,
            importance_score: +(0.7 - i * 0.06).toFixed(2),
        }));
        const total_memories = Math.max(authored.length, Math.round(c.count * 0.18));
        return {
            handoff_record_id: `hnd_${Date.now().toString(36)}`,
            departing_user_id: c.id,
            departing_user_name: c.name,
            total_memories,
            tier_1_critical,
            tier_2_important,
            tier_3_standard_count: Math.max(
                0,
                total_memories - tier_1_critical.length - tier_2_important.length
            ),
        };
    }, { min: 500, max: 900 }),

    // GET /v1/workspaces/:id/analytics/who-would-know?q=<query>
    whoWouldKnow: (_wsId, { q = "" } = {}) => request(() => {
        const ql = q.trim().toLowerCase();
        // Tokenize query — match if ANY word appears in content/tags/category.
        // This mirrors real BM25 behavior: "database migrations" matches
        // memories containing "database" OR "migrations" OR "migration".
        const tokens = ql.split(/\s+/).filter(w => w.length >= 3);
        const matchMemory = (m) => {
            if (!ql) return true;
            const haystack = (
                m.content + " " + (m.tags || []).join(" ") + " " + m.category
            ).toLowerCase();
            return tokens.some(t => haystack.includes(t));
        };
        // Score each contributor by how much their memories match, weighted
        // by (token-match count × contributor base score).
        const scored = CONTRIBUTORS.map(c => {
            const authored = MEMORIES.filter(m =>
                m.attribution[0]?.author === c.login
                || m.attribution.some(a => a.author === c.login)
            );
            const matched = authored.filter(matchMemory);
            return { c, matched, score: matched.length * c.score };
        }).filter(x => x.score > 0).sort((a, b) => b.score - a.score).slice(0, 5);
        const topScore = scored[0]?.score || 1;
        return {
            query: q,
            experts: scored.map(({ c, matched, score }) => ({
                user_id:         c.id,
                name:            c.name,
                login:           c.login,
                confidence:      Math.round((score / topScore) * 100) / 100,
                memory_count:    matched.length || 1,
                peak_attribution: c.score,
                top_memory: matched[0] ? {
                    id:       matched[0].memory_id,
                    preview:  matched[0].content.replace(/[#*`]/g, "").slice(0, 110).trim(),
                    category: matched[0].category,
                } : { id: "", preview: "No preview", category: c.top_category },
            })),
        };
    }, { min: 100, max: 280 }),

    // Analytics extras
    getMemoriesOverTime: () => request(() => ({ series: MEMORIES_OVER_TIME })),
    getSearchActivity:   () => request(() => ({ series: SEARCH_ACTIVITY })),

    // Team
    // The signed-in mock user (usr_01) is the first member, so the demo owner
    // is recognised as one by the owner/admin checks.
    listTeamMembers: () => request(() => ({
        members: TEAM_MEMBERS.map((m, i) => (i === 0 ? { ...m, id: CURRENT_USER.id } : m)),
    })),
    listContributors: () => request(() => ({ contributors: CONTRIBUTORS })),
};

export default mockApi;
