// Real backend client — fetches `${REACT_APP_BACKEND_URL}/v1/*` endpoints.
// Endpoint paths + response adapters verified against the backend on 2026-04-21.
//
// Some response shapes differ from what the UI consumes; adapters here convert
// real-backend responses into the canonical UI shape (the same shape the mock
// client produces), so UI components don't need to know which backend they're
// talking to.

import { mockApi } from "./mockApi";

const BASE = (process.env.REACT_APP_BACKEND_URL || "").replace(/\/+$/, "");
const DEFAULT_WS = process.env.REACT_APP_DEFAULT_WORKSPACE_ID || "ws_acme_platform";
const wsPath = (wsId) => encodeURIComponent(wsId || DEFAULT_WS);

// Deterministic palette — used when the backend doesn't include a color.
const PALETTE = ["#4F7EFF", "#A78BFA", "#34D399", "#F59E0B", "#EF4444", "#60A5FA", "#F472B6", "#2DD4BF"];
const colorFor = (key, i = 0) => {
    if (typeof i === "number" && i >= 0) return PALETTE[i % PALETTE.length];
    const s = String(key || "");
    let h = 0;
    for (let k = 0; k < s.length; k++) h = (h * 31 + s.charCodeAt(k)) | 0;
    return PALETTE[Math.abs(h) % PALETTE.length];
};

const ACTION_COLOR = {
    memory_created:    "#4F7EFF",
    memory_edited:     "#4F7EFF",
    conflict_opened:   "#F59E0B",
    conflict_detected: "#F59E0B",
    conflict_resolved: "#34D399",
    connector_synced:  "#34D399",
    handoff_started:   "#A78BFA",
    handoff_initiated: "#A78BFA",
};

class ApiError extends Error {
    constructor(status, message, body) {
        super(message);
        this.status = status;
        this.body = body;
    }
}

async function request(path, { method = "GET", body, params } = {}) {
    const url = new URL(BASE + path, window.location.origin);
    if (params) {
        for (const [k, v] of Object.entries(params)) {
            if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
        }
    }
    const t0 = performance.now();
    const res = await fetch(url.toString(), {
        method,
        credentials: "include",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: body ? JSON.stringify(body) : undefined,
    });
    const latency = Math.round(performance.now() - t0);
    const text = await res.text();
    const data = text ? JSON.parse(text) : {};
    if (!res.ok) throw new ApiError(res.status, `${res.status} ${res.statusText}`, data);
    return { ...data, __latency_ms: latency };
}

// ---------- adapters ----------

function adaptOverview(r) {
    // Backend returns health_breakdown values as decimals (0.0–1.0) with key
    // `attribution`; the UI wants percentages (0–100) with key `attribution_coverage`.
    const hb = r.health_breakdown || {};
    const toPct = (v) => (v == null ? 0 : Math.round(v * 100));
    const health_breakdown = {
        coverage:             toPct(hb.coverage),
        freshness:            toPct(hb.freshness),
        conflict_ratio:       toPct(hb.conflict_ratio),
        attribution_coverage: toPct(hb.attribution ?? hb.attribution_coverage),
    };

    // top_contributors: {user_id, name, memory_count, avg_contribution_pct} →
    //                   {name, count, color}
    const top_contributors = (r.top_contributors || []).map((c, i) => ({
        name:  c.name,
        count: c.memory_count ?? c.count ?? 0,
        color: colorFor(c.user_id || c.name, i),
    }));

    // recent_activity: backend now returns `description` directly.
    // We keep the `action` field to drive color coding.
    const recent_activity = (r.recent_activity || []).map((a) => ({
        type:        a.action || a.type || "memory_created",
        description: a.description || "",
        color:       ACTION_COLOR[a.action] || "#4F7EFF",
        timestamp:   a.timestamp,
    }));

    return {
        ...r,
        health_breakdown,
        top_contributors,
        recent_activity,
    };
}

function adaptContributor(c, i) {
    // Backend shape:
    //   {user_id, login, name, total_memories_created, total_memories_influenced,
    //    avg_contribution_pct, collaboration_rate, last_contribution_at}
    // avg_contribution_pct may arrive as either 0–1 or 0–100; normalise to 0–1.
    const pct = c.avg_contribution_pct ?? c.score ?? 0;
    return {
        id:           c.user_id || c.id,
        login:        c.login,
        name:         c.name,
        count:        c.total_memories_influenced ?? c.total_memories_created ?? c.count ?? 0,
        score:        pct > 1 ? pct / 100 : pct,
        last_active:  c.last_contribution_at || c.last_active || null,
        avatarColor:  colorFor(c.user_id || c.login, i),
        top_category: "",
    };
}

// ---------- public client ----------

export const realApi = {
    // ----- session -----
    getCurrentUser: () => request(`/v1/team/me`),
    getWorkspace: (wsId = DEFAULT_WS) => request(`/v1/workspaces/${wsPath(wsId)}`),

    // ----- analytics -----
    getAnalyticsOverview: async (wsId = DEFAULT_WS) => {
        const r = await request(`/v1/workspaces/${wsPath(wsId)}/analytics/overview`);
        return adaptOverview(r);
    },
    getKnowledgeGaps: (wsId = DEFAULT_WS) =>
        request(`/v1/workspaces/${wsPath(wsId)}/analytics/knowledge-gaps`),
    getContributionMap: async (wsId = DEFAULT_WS) => {
        const r = await request(`/v1/workspaces/${wsPath(wsId)}/analytics/contribution-map`);
        const contributors = (r.contributors || []).map(adaptContributor);
        return { ...r, contributors };
    },
    // These two endpoints don't exist on the backend yet — fall back to mock
    // data so the Analytics page still renders. Remove once backend ships
    // `/analytics/memories-over-time` and `/analytics/search-activity`.
    getMemoriesOverTime: (wsId) => mockApi.getMemoriesOverTime(wsId),
    getSearchActivity:   (wsId) => mockApi.getSearchActivity(wsId),

    // ----- memories -----
    searchMemories: ({ query = "", mode = "hybrid", limit = 20, workspace_ids } = {}) =>
        request(`/v1/memories/search`, {
            method: "POST",
            body: { query, mode, limit, workspace_ids: workspace_ids || [DEFAULT_WS] },
        }),
    getMemory: (id) => request(`/v1/memories/${encodeURIComponent(id)}`),
    createMemory: (payload) =>
        request(`/v1/memories`, {
            method: "POST",
            body: { workspace_id: DEFAULT_WS, ...payload },
        }),
    getJobStatus: (jobId) => request(`/v1/memories/jobs/${encodeURIComponent(jobId)}`),
    // Also available on the backend:
    //   PATCH  /v1/memories/{id}, DELETE /v1/memories/{id},
    //   GET    /v1/memories/{id}/versions, /attribution, /edits

    // ----- conflicts -----
    listConflicts: (wsId = DEFAULT_WS, { status = "all" } = {}) =>
        request(`/v1/workspaces/${wsPath(wsId)}/conflicts`, { params: { status } }),
    getConflict: (id) => request(`/v1/conflicts/${encodeURIComponent(id)}`),
    reviewConflict: (id) =>
        request(`/v1/conflicts/${encodeURIComponent(id)}/review`, { method: "POST" }),
    resolveConflict: (id, { resolution_type, note }) =>
        request(`/v1/conflicts/${encodeURIComponent(id)}/resolve`, {
            method: "POST",
            body: { resolution_type, note },
        }),

    // ----- connectors -----
    listConnectors: (wsId = DEFAULT_WS) =>
        request(`/v1/workspaces/${wsPath(wsId)}/connectors`),
    createConnector: (wsId = DEFAULT_WS, payload) =>
        request(`/v1/workspaces/${wsPath(wsId)}/connectors`, { method: "POST", body: payload }),
    getConnector: (id) => request(`/v1/connectors/${encodeURIComponent(id)}`),
    updateConnector: (id, payload) =>
        request(`/v1/connectors/${encodeURIComponent(id)}`, { method: "PATCH", body: payload }),
    deleteConnector: (id) =>
        request(`/v1/connectors/${encodeURIComponent(id)}`, { method: "DELETE" }),
    triggerSync: (connId) =>
        request(`/v1/connectors/${encodeURIComponent(connId)}/sync`, { method: "POST" }),
    getSyncLogs: (connId) =>
        request(`/v1/connectors/${encodeURIComponent(connId)}/sync-logs`),

    // ----- handoffs (team namespace) -----
    // `classifyHandoff` returns the new tier_1/2/3 shape:
    //   { handoff_record_id, departing_user_id, departing_user_name, total_memories,
    //     tier_1_critical: [{memory_id, content, importance_score,
    //                        suggested_successor_id, suggested_successor_name,
    //                        successor_confidence}],
    //     tier_2_important: [{memory_id, content, importance_score}],
    //     tier_3_standard_count: number }
    classifyHandoff: ({ departing_id, receiving_id, departure_date, notes, wsId = DEFAULT_WS } = {}) =>
        request(`/v1/team/workspaces/${wsPath(wsId)}/handoff/initiate`, {
            method: "POST",
            body: { departing_id, receiving_id, departure_date, notes },
        }),
    assignHandoff: (wsId = DEFAULT_WS, payload) =>
        request(`/v1/team/workspaces/${wsPath(wsId)}/handoff/assign`, { method: "POST", body: payload }),
    completeHandoff: (wsId = DEFAULT_WS, payload) =>
        request(`/v1/team/workspaces/${wsPath(wsId)}/handoff/complete`, { method: "POST", body: payload }),
    listHandoffs: (wsId = DEFAULT_WS) =>
        request(`/v1/workspaces/${wsPath(wsId)}/handoffs`),

    // GET /v1/workspaces/:id/analytics/who-would-know?q=<query>
    whoWouldKnow: async (wsId = DEFAULT_WS, { q = "", limit = 5 } = {}) => {
        const r = await request(`/v1/workspaces/${wsPath(wsId)}/analytics/who-would-know`, {
            params: { q, limit },
        });
        // Attach colors not returned by the backend
        const experts = (r.experts || []).map((e, i) => ({
            ...e,
            avatarColor: colorFor(e.user_id || e.login, i),
        }));
        return { ...r, experts };
    },

    // ----- team / contributors -----
    listTeamMembers: (wsId = DEFAULT_WS) =>
        request(`/v1/workspaces/${wsPath(wsId)}/members`),
    listContributors: async (wsId = DEFAULT_WS) => {
        const r = await request(`/v1/workspaces/${wsPath(wsId)}/analytics/contribution-map`);
        const contributors = (r.contributors || []).map(adaptContributor);
        return { contributors, __latency_ms: r.__latency_ms };
    },
};

export default realApi;
export { ApiError };
