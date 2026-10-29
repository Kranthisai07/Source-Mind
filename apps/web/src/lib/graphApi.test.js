/**
 * The four graph-backed reads: what the real adapter puts on the wire, and that the mock answers
 * with the same shapes (so pages developed against the mock work against the API).
 */

import { realApi, resetIdentityScopedCaches } from "./realApi";
import { mockApi } from "./mockApi";
import { setTokenGetter } from "./authToken";

const WS = "11111111-2222-4333-8444-555555555555";
const MEMORY = "6f1d2c3a-0000-4000-8000-000000000001";

function captureFetch({ status = 200, body = {} } = {}) {
    const calls = [];
    global.fetch = jest.fn(async (url, init) => {
        calls.push({ url: String(url), init });
        return {
            ok: status >= 200 && status < 300,
            status,
            statusText: String(status),
            headers: { get: () => null },
            text: async () => JSON.stringify(body),
        };
    });
    return calls;
}

beforeEach(() => setTokenGetter(null));
afterEach(() => {
    delete global.fetch;
    resetIdentityScopedCaches();
    jest.restoreAllMocks();
});

describe("realApi graph reads", () => {
    test("lineage and owners are plain GETs of the memory, with the id encoded", async () => {
        const calls = captureFetch({ body: { data: { memory_id: "a/b", source: "graph", versions: [] } } });
        await realApi.getMemoryLineage("a/b");
        await realApi.getMemoryOwners("a/b");
        expect(calls.map((c) => new URL(c.url).pathname)).toEqual([
            "/v1/memories/a%2Fb/lineage",
            "/v1/memories/a%2Fb/owners",
        ]);
        expect(calls.every((c) => (c.init?.method || "GET") === "GET")).toBe(true);
    });

    test("related sends depth and limit, and repeats `types` once per type", async () => {
        const calls = captureFetch({ body: { data: { memory_id: MEMORY, nodes: [], edges: [] } } });
        await realApi.getRelatedMemories(MEMORY, { depth: 2, types: ["updates", "extends"], limit: 10 });
        const url = new URL(calls[0].url);
        expect(url.pathname).toBe(`/v1/memories/${MEMORY}/related`);
        expect(url.searchParams.get("depth")).toBe("2");
        expect(url.searchParams.get("limit")).toBe("10");
        expect(url.searchParams.getAll("types")).toEqual(["updates", "extends"]);
    });

    test("related with no options asks for one hop and omits the filters", async () => {
        const calls = captureFetch({ body: { data: { memory_id: MEMORY, nodes: [], edges: [] } } });
        await realApi.getRelatedMemories(MEMORY);
        const url = new URL(calls[0].url);
        expect(url.searchParams.get("depth")).toBe("1");
        expect(url.searchParams.has("types")).toBe(false);
        expect(url.searchParams.has("limit")).toBe(false);
    });

    test("the collaboration graph is workspace-scoped, and the envelope is unwrapped", async () => {
        const calls = captureFetch({
            body: { data: { source: "postgres", nodes: [], edges: [], truncated: false } },
        });
        const result = await realApi.getCollaborationGraph(WS);
        expect(new URL(calls[0].url).pathname).toBe(`/v1/workspaces/${WS}/analytics/collaboration-graph`);
        expect(result.source).toBe("postgres");
        expect(result.data).toBeUndefined();
    });

    test("a memory the caller cannot see is a rejection carrying the status, not an empty graph", async () => {
        captureFetch({ status: 404, body: { detail: "not found" } });
        await expect(realApi.getRelatedMemories(MEMORY)).rejects.toMatchObject({ status: 404 });
    });
});

describe("mock graph reads match the real response shapes", () => {
    const keys = (o) => Object.keys(o).filter((k) => k !== "__latency_ms").sort();

    test("related", async () => {
        const memory = (await mockApi.listMemories?.())?.memories?.[0]?.memory_id || "mem_1001";
        const r = await mockApi.getRelatedMemories(memory, { depth: 2 });
        expect(keys(r)).toEqual(expect.arrayContaining(["depth_requested", "depth_served", "edges", "memory_id", "nodes", "source"]));
        expect(keys(r.nodes[0])).toEqual(["hops", "id", "importance", "is_current", "preview", "version"]);
        expect(keys(r.edges[0])).toEqual(expect.arrayContaining(["confidence", "id", "source", "target", "type"]));
        const ids = new Set(r.nodes.map((n) => n.id));
        expect(r.nodes.filter((n) => n.hops === 0).map((n) => n.id)).toEqual([r.memory_id]);
        r.edges.forEach((e) => {
            expect(ids.has(e.source)).toBe(true);
            expect(ids.has(e.target)).toBe(true);
        });
    });

    test("related honours depth, and an unknown memory is a 404 like the API", async () => {
        const one = await mockApi.getRelatedMemories("mem_1001", { depth: 1 });
        const three = await mockApi.getRelatedMemories("mem_1001", { depth: 3 });
        expect(three.nodes.length).toBeGreaterThan(one.nodes.length);
        expect(Math.max(...one.nodes.map((n) => n.hops))).toBe(1);
        await expect(mockApi.getRelatedMemories("nope")).rejects.toMatchObject({ status: 404 });
    });

    test("lineage and owners", async () => {
        const lineage = await mockApi.getMemoryLineage("mem_1001");
        expect(keys(lineage)).toEqual(["memory_id", "source", "versions"]);
        expect(keys(lineage.versions[0])).toEqual(["contributors", "created_at", "id", "is_current", "version"]);
        expect(lineage.versions.filter((v) => v.is_current)).toHaveLength(1);

        const owners = await mockApi.getMemoryOwners("mem_1001");
        expect(keys(owners)).toEqual(["depth_served", "memory_id", "owners", "source"]);
        expect(keys(owners.owners[0])).toEqual(
            expect.arrayContaining(["direct_weight", "display_name", "hops", "path", "score", "user_id"])
        );
        const scores = owners.owners.map((o) => o.score);
        expect([...scores].sort((a, b) => b - a)).toEqual(scores);
    });

    test("collaboration", async () => {
        const g = await mockApi.getCollaborationGraph();
        expect(keys(g)).toEqual(["edges", "nodes", "source", "truncated"]);
        expect(keys(g.nodes[0])).toEqual(["display_name", "memory_count", "user_id"]);
        const ids = new Set(g.nodes.map((n) => n.user_id));
        g.edges.forEach((e) => {
            expect(keys(e)).toEqual(["shared_memories", "source", "target"]);
            expect(ids.has(e.source) && ids.has(e.target)).toBe(true);
        });
    });
});
