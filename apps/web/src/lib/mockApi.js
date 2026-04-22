// Mock API client that matches the exact SourceMind backend contract shapes.
// Swap the `request()` helper with real fetch calls to /v1/... endpoints later —
// UI components will not need any changes.

import {
    WORKSPACE, CURRENT_USER, CONTRIBUTORS, MEMORIES, CONFLICTS,
    KNOWLEDGE_GAPS, CONNECTORS, SYNC_LOGS, HANDOFFS, RECENT_ACTIVITY,
    MEMORIES_OVER_TIME, SEARCH_ACTIVITY, TEAM_MEMBERS,
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

// ----- workspace -----
export const mockApi = {
    getCurrentUser: () => request(() => CURRENT_USER),
    getWorkspace:   () => request(() => WORKSPACE),

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
    searchMemories: ({ query = "", mode = "hybrid", limit = 20 } = {}) =>
        request(() => {
            const q = query.trim().toLowerCase();
            let results = MEMORIES;
            if (q) {
                results = MEMORIES.filter(m =>
                    m.content.toLowerCase().includes(q) ||
                    m.tags.some(t => t.toLowerCase().includes(q)) ||
                    m.category.toLowerCase().includes(q)
                );
            }
            return {
                results: results.slice(0, limit),
                total_found: results.length,
                mode,
            };
        }, { min: 40, max: 90 }),

    // GET /v1/memories/:id
    getMemory: (id) => request(() => {
        const m = MEMORIES.find(x => x.memory_id === id);
        return m || { error: "not_found" };
    }),

    // POST /v1/memories (returns job_id)
    createMemory: (_payload) => request(() => ({
        job_id: `job_${Date.now().toString(36)}`,
    }), { min: 120, max: 240 }),

    // GET /v1/memories/jobs/:id — progressive pipeline status
    // We use an in-memory job tracker so the same job id returns advancing stages.
    _jobStart: {},
    getJobStatus(jobId) {
        if (!this._jobStart[jobId]) this._jobStart[jobId] = Date.now();
        const elapsed = Date.now() - this._jobStart[jobId];
        const stages = ["receive", "extract", "chunk", "embed", "store", "attribute", "done"];
        const stageDurations = [200, 450, 300, 700, 250, 400, 100]; // ms
        let total = 0;
        let currentIdx = 0;
        for (let i = 0; i < stages.length; i++) {
            total += stageDurations[i];
            if (elapsed < total) { currentIdx = i; break; }
            currentIdx = i;
        }
        const stageStatuses = stages.map((name, i) => {
            let status = "pending";
            if (i < currentIdx) status = "done";
            else if (i === currentIdx) status = elapsed >= total ? "done" : "running";
            return { name, status, elapsed_ms: i <= currentIdx ? Math.min(elapsed - stageDurations.slice(0, i).reduce((a, b) => a + b, 0), stageDurations[i]) : 0 };
        });
        return request(() => ({
            job_id: jobId,
            status: elapsed >= total ? "done" : "running",
            stage: stages[currentIdx],
            elapsed_ms: elapsed,
            stages: stageStatuses,
            error: null,
        }), { min: 60, max: 120 });
    },

    // GET /v1/workspaces/:id/conflicts
    listConflicts: (_wsId, { status = "all" } = {}) => request(() => ({
        conflicts: CONFLICTS.filter(c => status === "all" ? true : c.status === status),
    })),

    // GET /v1/conflicts/:id
    getConflict: (id) => request(() => {
        const c = CONFLICTS.find(x => x.id === id);
        return c || { error: "not_found" };
    }),

    // POST /v1/conflicts/:id/resolve
    resolveConflict: (_id, { resolution_type, note }) => request(() => ({
        ok: true, resolution_type, note,
    }), { min: 250, max: 500 }),

    // GET /v1/workspaces/:id/connectors
    listConnectors: (_wsId) => request(() => ({
        connectors: CONNECTORS,
    })),

    triggerSync: (_connId) => request(() => ({ ok: true }), { min: 300, max: 600 }),
    getSyncLogs: (_connId) => request(() => ({ logs: SYNC_LOGS })),

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

    // Analytics extras
    getMemoriesOverTime: () => request(() => ({ series: MEMORIES_OVER_TIME })),
    getSearchActivity:   () => request(() => ({ series: SEARCH_ACTIVITY })),

    // Team
    listTeamMembers: () => request(() => ({ members: TEAM_MEMBERS })),
    listContributors: () => request(() => ({ contributors: CONTRIBUTORS })),
};

export default mockApi;
