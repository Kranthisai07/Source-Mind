// Real backend client — fetches `${REACT_APP_BACKEND_URL}/v1/*` endpoints.
// Endpoint paths + response adapters verified against the backend on 2026-04-21.
//
// Some response shapes differ from what the UI consumes; adapters here convert
// real-backend responses into the canonical UI shape (the same shape the mock
// client produces), so UI components don't need to know which backend they're
// talking to.

import { mockApi } from "./mockApi";
import { getAuthToken, logTokenFingerprintOnce } from "./authToken";

const BASE = (process.env.REACT_APP_BACKEND_URL || "").replace(/\/+$/, "");

// Every workspace-scoped route types its path parameter as `uuid.UUID`, so a
// non-UUID is rejected by Pydantic before the handler runs:
//
//   GET /v1/workspaces/ws_acme_platform/analytics/overview
//   422 {"field":"path.workspace_id",
//        "message":"Input should be a valid UUID, invalid character: found `w` at 1",
//        "type":"uuid_parsing"}
//
// The previous default was the literal "ws_acme_platform" — a mock-shaped id
// that could never satisfy this API, so all ~16 workspace-scoped calls 422'd
// identically. Resolution order is now: explicit argument, configured env var,
// then the first workspace the signed-in user can actually see.
const CONFIGURED_WS = (process.env.REACT_APP_DEFAULT_WORKSPACE_ID || "").trim();

let _wsPromise = null;

/**
 * The workspace UUID to scope requests to.
 *
 * The lookup is memoised as a promise, not a value, so concurrent callers on
 * first paint share one request instead of each firing their own. A failure
 * clears the memo so the next attempt retries rather than caching the error.
 */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

async function resolveWorkspaceId(explicit) {
    // An explicit id is honoured only if it could actually satisfy the route.
    // Several pages still pass mock-era placeholders — Dashboard.jsx sent
    // "ws_acme_platform", Analytics/Conflicts/Connectors/Handoff send "ws" —
    // which mockApi ignored entirely but which the real API rejects with a 422
    // before the handler runs. Falling through is strictly better than
    // forwarding a value that cannot work.
    if (explicit) {
        if (UUID_RE.test(explicit)) return explicit;
        if (process.env.NODE_ENV !== "production") {
            // eslint-disable-next-line no-console
            console.warn(
                `[SourceMind] Ignoring non-UUID workspace id ${JSON.stringify(explicit)} ` +
                "— falling back to the configured/resolved workspace."
            );
        }
    }
    if (CONFIGURED_WS) return CONFIGURED_WS;

    if (!_wsPromise) {
        _wsPromise = request("/v1/workspaces")
            .then((r) => {
                // This route IS enveloped: {data: [...], meta: {...}}.
                const list = Array.isArray(r.data) ? r.data : [];
                if (!list.length) {
                    throw new ApiError(
                        404,
                        "This account has no workspaces. Create one, or set " +
                        "REACT_APP_DEFAULT_WORKSPACE_ID in apps/web/.env.",
                        r
                    );
                }
                return list[0].id;
            })
            .catch((err) => {
                _wsPromise = null;
                throw err;
            });
    }
    return _wsPromise;
}

// Deterministic palette — used when the backend doesn't include a color.
const PALETTE = ["#4F7EFF", "#A78BFA", "#34D399", "#F59E0B", "#EF4444", "#60A5FA", "#F472B6", "#2DD4BF"];
const colorFor = (key, i = 0) => {
    if (typeof i === "number" && i >= 0) return PALETTE[i % PALETTE.length];
    const s = String(key || "");
    let h = 0;
    for (let k = 0; k < s.length; k++) h = (h * 31 + s.charCodeAt(k)) | 0;
    return PALETTE[Math.abs(h) % PALETTE.length];
};

// Activity colour coding.
//
// This map was written against mockData, not against the API, and none of its
// original eight keys could ever match. The feed is built by get_overview from
// a single query over `attribution_edits`, so `action` is always one of the
// five AttributionActionType members - create, edit, approve, reject, merge -
// and every real event fell through to the default blue.
//
// The removed keys were not merely misspelled. conflict_opened,
// conflict_detected, conflict_resolved, connector_synced, handoff_started and
// handoff_initiated describe events this endpoint STRUCTURALLY cannot emit:
// nothing in the query touches memory_conflicts, connector_sync_logs or
// handoff_records. They encoded a unified multi-source activity feed that does
// not exist on the backend. Restoring them means changing get_overview, not
// this file.
//
// The mock vocabulary is kept alongside so the offline demo still renders in
// colour; mockData emits `type:` while the API emits `action`, and
// adaptOverview already reads `a.action || a.type`.
export const ACTION_COLOR = {
    // Real: AttributionActionType (attribution_edits.action_type)
    create:            "#4F7EFF",  // sm-blue
    edit:              "#4F7EFF",  // sm-blue
    approve:           "#34D399",  // sm-green
    reject:            "#EF4444",  // sm-red
    merge:             "#A78BFA",  // sm-purple

    // Mock-only, retained for REACT_APP_USE_MOCKS=true.
    memory_created:    "#4F7EFF",
    memory_edited:     "#4F7EFF",
    conflict_opened:   "#F59E0B",
    conflict_resolved: "#34D399",
    connector_synced:  "#34D399",
    handoff_started:   "#A78BFA",
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
    // Every backend route except /health is behind get_current_user, which
    // verifies a Clerk JWT against the instance JWKS. Without this header the
    // API answers 401 SM001 ("Authorization header required").
    //
    // Fetched per request, not cached: Clerk session tokens expire in about a
    // minute and getToken() refreshes them transparently.
    const token = await getAuthToken();
    if (token) logTokenFingerprintOnce(token);

    const headers = {
        "Content-Type": "application/json",
        Accept: "application/json",
    };
    if (token) headers.Authorization = `Bearer ${token}`;

    const t0 = performance.now();
    const res = await fetch(url.toString(), {
        method,
        credentials: "include",
        headers,
        body: body ? JSON.stringify(body) : undefined,
    });
    const latency = Math.round(performance.now() - t0);
    const text = await res.text();
    const data = text ? JSON.parse(text) : {};
    if (!res.ok) throw new ApiError(res.status, `${res.status} ${res.statusText}`, data);
    return { ...data, __latency_ms: latency };
}

// ---------- response envelope ----------
//
// The API is NOT uniform, and both directions matter: unwrapping a raw
// response is as wrong as failing to unwrap an enveloped one. Routes declared
// `response_model=APIResponse[T]` return {data, meta}; everything else returns
// its payload at the top level. Verified endpoint by endpoint against the
// running API rather than inferred:
//
//   ENVELOPED  /v1/team/me, /v1/workspaces, /v1/workspaces/{ws},
//              /v1/workspaces/{ws}/members, /v1/memories/{id},
//              POST /v1/memories, /v1/memories/jobs/{id}
//   RAW        all four /analytics/*, /conflicts, /connectors, /handoffs,
//              POST /v1/memories/search
//
// Only the ENVELOPED list goes through these helpers.

/** {data: {...}, meta} -> {...} , preserving the measured latency. */
function unwrapOne(r) {
    const d = r && r.data;
    if (d && typeof d === "object" && !Array.isArray(d)) {
        return { ...d, __latency_ms: r.__latency_ms };
    }
    return r;
}

/** {data: [...], meta} -> the array, preserving the measured latency. */
function unwrapList(r) {
    return {
        items: Array.isArray(r && r.data) ? r.data : [],
        __latency_ms: r && r.__latency_ms,
    };
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

    // knowledge_health_score is a 0.0-1.0 fraction, like the breakdown values,
    // but the UI renders it out of 100 in two places: the page subtitle
    // ("health {score}/100") and HealthGauge, which animates Math.round(score)
    // straight onto a /100 dial. Converting the breakdown while leaving this
    // raw is what produced "health 0.25/100" beside a gauge reading 0.
    // mockApi already returns this on a 0-100 scale, so converting here also
    // makes the two clients agree.
    const knowledge_health_score = toPct(r.knowledge_health_score);

    return {
        ...r,
        knowledge_health_score,
        health_breakdown,
        top_contributors,
        recent_activity,
    };
}

/**
 * ContributionBreakdown[] -> the shape MemoryDetail renders.
 *
 * Backend: {user: {id, display_name, avatar_url}, contribution_weight,
 *           percentage, signals, is_primary}
 * UI:      {author, name, score, percentage, is_primary, color, signals}
 *
 * Two fields have no real equivalent and must not be faked:
 *   - there is no login handle; UserSummary carries id/display_name/avatar_url
 *     only, so `author` falls back to the display name.
 *   - `signals` is null unless the caller is owner/admin, so the per-signal
 *     column has to tolerate its absence rather than assume five numbers.
 */
function adaptAttribution(list) {
    if (!Array.isArray(list)) return [];
    return list.map((a, i) => ({
        author: a.user?.display_name || a.user?.id || "unknown",
        name: a.user?.display_name || null,
        avatar_url: a.user?.avatar_url || null,
        score:
            a.contribution_weight ??
            (a.percentage != null ? a.percentage / 100 : 0),
        percentage: a.percentage ?? null,
        is_primary: Boolean(a.is_primary),
        color: colorFor(a.user?.id || a.user?.display_name, i),
        signals: a.signals || null,
    }));
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
    getCurrentUser: async () => unwrapOne(await request(`/v1/team/me`)),
    getWorkspace: async (wsId) =>
        unwrapOne(await request(`/v1/workspaces/${await resolveWorkspaceId(wsId)}`)),

    // ----- analytics -----
    // These four routes return a RAW dict, not the {data, meta} envelope used
    // by /v1/team/me and /v1/workspaces, so the adapters read fields straight
    // off the response. Verified against the deployed API.
    getAnalyticsOverview: async (wsId) => {
        const ws = await resolveWorkspaceId(wsId);
        const r = await request(`/v1/workspaces/${ws}/analytics/overview`);
        return adaptOverview(r);
    },
    getKnowledgeGaps: async (wsId) =>
        request(`/v1/workspaces/${await resolveWorkspaceId(wsId)}/analytics/knowledge-gaps`),
    getContributionMap: async (wsId) => {
        const ws = await resolveWorkspaceId(wsId);
        const r = await request(`/v1/workspaces/${ws}/analytics/contribution-map`);
        const contributors = (r.contributors || []).map(adaptContributor);
        return { ...r, contributors };
    },
    // These two endpoints don't exist on the backend yet — fall back to mock
    // data so the Analytics page still renders. Remove once backend ships
    // `/analytics/memories-over-time` and `/analytics/search-activity`.
    getMemoriesOverTime: (wsId) => mockApi.getMemoriesOverTime(wsId),
    getSearchActivity:   (wsId) => mockApi.getSearchActivity(wsId),

    // ----- memories -----
    // workspace_id is a QUERY parameter, singular, typed UUID:
    //
    //   workspace_id: UUID = Query(default=_DEV_WORKSPACE_ID, ...)
    //   effective_ws_id = current_user.workspace_id or workspace_id
    //
    // SearchRequest declares only {query, mode, limit, min_similarity,
    // include_attribution, filters} — there is no workspace_ids field, so the
    // array this used to put in the body was dropped by Pydantic and the route
    // fell through to its default, _DEV_WORKSPACE_ID
    // (00000000-0000-4000-8000-000000000010). Search returned HTTP 200 with
    // real-looking results from the Dev Workspace instead of the user's.
    //
    // current_user.workspace_id is never assigned in the auth path — it stays
    // None — so the query parameter is what actually decides, and omitting it
    // silently searches the wrong workspace rather than erroring.
    searchMemories: async ({
        query = "",
        mode = "hybrid",
        limit = 20,
        workspace_id,
    } = {}) =>
        request(`/v1/memories/search`, {
            method: "POST",
            params: { workspace_id: await resolveWorkspaceId(workspace_id) },
            body: { query, mode, limit },
        }),
    // include_attribution must be asked for explicitly — it defaults to false,
    // and MemoryDetail renders the breakdown as a primary element of the page.
    getMemory: async (id) => {
        const r = unwrapOne(
            await request(`/v1/memories/${encodeURIComponent(id)}`, {
                params: { include_attribution: true },
            })
        );
        return { ...r, attribution: adaptAttribution(r.attribution) };
    },
    // workspace_id is set AFTER the spread so a caller cannot silently
    // override it with a placeholder — the previous order let Memories.jsx's
    // hardcoded "ws_acme_platform" win. A caller that genuinely wants another
    // workspace passes a real UUID, which resolveWorkspaceId honours.
    createMemory: async (payload = {}) =>
        unwrapOne(
            await request(`/v1/memories`, {
                method: "POST",
                body: {
                    ...payload,
                    workspace_id: await resolveWorkspaceId(payload.workspace_id),
                },
            })
        ),
    getJobStatus: async (jobId) => {
        const r = unwrapOne(
            await request(`/v1/memories/jobs/${encodeURIComponent(jobId)}`)
        );
        // Normalise the terminal state to the vocabulary the UI polls for.
        return { ...r, status: r.status === "completed" ? "done" : r.status };
    },
    // Also available on the backend:
    //   PATCH  /v1/memories/{id}, DELETE /v1/memories/{id},
    //   GET    /v1/memories/{id}/versions, /attribution, /edits

    // ----- conflicts -----
    listConflicts: async (wsId, { status = "all" } = {}) =>
        request(`/v1/workspaces/${await resolveWorkspaceId(wsId)}/conflicts`, {
            params: { status },
        }),
    getConflict: (id) => request(`/v1/conflicts/${encodeURIComponent(id)}`),
    reviewConflict: (id) =>
        request(`/v1/conflicts/${encodeURIComponent(id)}/review`, { method: "POST" }),
    resolveConflict: (id, { resolution_type, note }) =>
        request(`/v1/conflicts/${encodeURIComponent(id)}/resolve`, {
            method: "POST",
            body: { resolution_type, note },
        }),

    // ----- connectors -----
    listConnectors: async (wsId) => {
        const r = await request(
            `/v1/workspaces/${await resolveWorkspaceId(wsId)}/connectors`
        );
        return { ...r, connectors: r.items || r.connectors || [] };
    },
    createConnector: async (wsId, payload) =>
        request(`/v1/workspaces/${await resolveWorkspaceId(wsId)}/connectors`, {
            method: "POST",
            body: payload,
        }),
    getConnector: (id) => request(`/v1/connectors/${encodeURIComponent(id)}`),
    updateConnector: (id, payload) =>
        request(`/v1/connectors/${encodeURIComponent(id)}`, { method: "PATCH", body: payload }),
    deleteConnector: (id) =>
        request(`/v1/connectors/${encodeURIComponent(id)}`, { method: "DELETE" }),
    triggerSync: (connId) =>
        request(`/v1/connectors/${encodeURIComponent(connId)}/sync`, { method: "POST" }),
    getSyncLogs: (connId) =>
        request(`/v1/connectors/${encodeURIComponent(connId)}/sync-logs`),

    // ----- handoffs -----
    //
    // NOT a /team route. handoff_router is declared `APIRouter(tags=["handoff"])`
    // with no prefix and mounted at /v1, so the real paths are
    // /v1/workspaces/{id}/handoff/{initiate,assign,complete}. The previous
    // /v1/team/... URLs 404'd. (listHandoffs below was already correct.)
    //
    // The field names were wrong too, so fixing only the path would have turned
    // a 404 into a 422: InitiateHandoffBody accepts exactly
    // {departing_user_id}, while the page and mockApi both speak
    // `departing_id`. receiving_id / departure_date / notes are not part of the
    // initiate contract at all — the backend classifies memories for the
    // departing user and the receiver is chosen later, per-memory, via assign.
    // They are dropped here rather than sent and silently ignored.
    //
    // Returns: { handoff_record_id, departing_user_id, departing_user_name,
    //            total_memories,
    //            tier_1_critical: [{memory_id, content, importance_score,
    //                               suggested_successor_id,
    //                               suggested_successor_name,
    //                               successor_confidence}],
    //            tier_2_important: [{memory_id, content, importance_score}],
    //            tier_3_standard_count }
    classifyHandoff: async ({ departing_id, wsId } = {}) =>
        request(`/v1/workspaces/${await resolveWorkspaceId(wsId)}/handoff/initiate`, {
            method: "POST",
            body: { departing_user_id: departing_id },
        }),

    // AssignHandoffBody: {memory_id, new_owner_id, handoff_record_id, note?}
    assignHandoff: async (wsId, payload) =>
        request(`/v1/workspaces/${await resolveWorkspaceId(wsId)}/handoff/assign`, {
            method: "POST",
            body: payload,
        }),

    // CompleteHandoffBody: {departing_user_id, handoff_record_id}
    completeHandoff: async (wsId, payload) =>
        request(`/v1/workspaces/${await resolveWorkspaceId(wsId)}/handoff/complete`, {
            method: "POST",
            body: payload,
        }),
    listHandoffs: async (wsId) =>
        request(`/v1/workspaces/${await resolveWorkspaceId(wsId)}/handoffs`),

    // GET /v1/workspaces/:id/analytics/who-would-know?q=<query>
    whoWouldKnow: async (wsId, { q = "", limit = 5 } = {}) => {
        const ws = await resolveWorkspaceId(wsId);
        const r = await request(`/v1/workspaces/${ws}/analytics/who-would-know`, {
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
    listTeamMembers: async (wsId) => {
        const r = unwrapList(
            await request(`/v1/workspaces/${await resolveWorkspaceId(wsId)}/members`)
        );
        return { members: r.items, __latency_ms: r.__latency_ms };
    },
    listContributors: async (wsId) => {
        const ws = await resolveWorkspaceId(wsId);
        const r = await request(`/v1/workspaces/${ws}/analytics/contribution-map`);
        const contributors = (r.contributors || []).map(adaptContributor);
        return { contributors, __latency_ms: r.__latency_ms };
    },
};

export default realApi;
export { ApiError };
