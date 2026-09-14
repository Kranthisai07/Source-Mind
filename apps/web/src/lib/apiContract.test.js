/**
 * Two contract tests against the real adapter, plus one safety property.
 *
 * Both integrations they cover were silently broken in ways no error surfaced:
 * a resolution note that Pydantic dropped because the key was not on the model,
 * and a version history that was read off a field the response never contained.
 * Neither produced a failure — one discarded user input, the other rendered an
 * empty state. So both are pinned here against the wire format rather than
 * against a belief about it.
 */

import fs from "fs";
import path from "path";

import { realApi } from "./realApi";
import { setTokenGetter } from "./authToken";

const SRC = path.resolve(__dirname, "..");

/** Capture the single fetch the adapter performs, and reply with `body`. */
function captureFetch({ status = 200, body = {}, headers = {} } = {}) {
    const calls = [];
    global.fetch = jest.fn(async (url, init) => {
        calls.push({ url: String(url), init });
        return {
            ok: status >= 200 && status < 300,
            status,
            statusText: String(status),
            headers: { get: (k) => headers[k] ?? headers[k.toLowerCase()] ?? null },
            text: async () => JSON.stringify(body),
        };
    });
    return calls;
}

beforeEach(() => {
    // No Clerk in tests; the adapter simply omits the Authorization header.
    setTokenGetter(null);
});

afterEach(() => {
    delete global.fetch;
    jest.restoreAllMocks();
});

describe("conflict resolution transmits the note", () => {
    test("the note is sent as resolution_note, the key ResolveBody declares", async () => {
        const calls = captureFetch({ body: { status: "ok" } });

        await realApi.resolveConflict("c-1", {
            resolution_type: "kept_a",
            note: "Kept A: the IVFFlat decision was superseded.",
        });

        expect(calls).toHaveLength(1);
        const sent = JSON.parse(calls[0].init.body);

        // The defect: `note` is not a field on ResolveBody, so Pydantic
        // dropped it and every note the user typed was discarded silently.
        expect(sent).toHaveProperty("resolution_note");
        expect(sent.resolution_note).toBe("Kept A: the IVFFlat decision was superseded.");
        expect(sent).not.toHaveProperty("note");
        expect(sent.resolution_type).toBe("kept_a");
    });

    test("an omitted note is sent as null, not as a missing key", async () => {
        const calls = captureFetch({ body: { status: "ok" } });
        await realApi.resolveConflict("c-1", { resolution_type: "kept_b" });
        const sent = JSON.parse(calls[0].init.body);
        // resolution_note is `str | None = None`; explicit null is valid and
        // keeps the payload shape stable.
        expect(sent.resolution_note).toBeNull();
    });

    test("it posts to the resolve route", async () => {
        const calls = captureFetch({ body: {} });
        await realApi.resolveConflict("abc", { resolution_type: "kept_a", note: "x" });
        expect(calls[0].url).toContain("/v1/conflicts/abc/resolve");
        expect(calls[0].init.method).toBe("POST");
    });
});

describe("memory version history", () => {
    test("loads the raw MemoryVersionsResponse without unwrapping", async () => {
        // The route is declared response_model=MemoryVersionsResponse, NOT
        // APIResponse[T], so there is no {data, meta} envelope to strip.
        const payload = {
            versions: [
                { id: "v2", version: 2, is_current: true, content: "newer", created_at: "2026-09-03T00:00:00Z" },
                { id: "v1", version: 1, is_current: false, content: "older", created_at: "2026-09-02T00:00:00Z" },
            ],
            total: 2,
        };
        captureFetch({ body: payload });

        const res = await realApi.getMemoryVersions("m-1");

        expect(res.versions).toHaveLength(2);
        expect(res.total).toBe(2);
        expect(res.versions[0].is_current).toBe(true);
        expect(res.versions[0].version).toBe(2);
    });

    test("requests the versions sub-resource of the given memory", async () => {
        const calls = captureFetch({ body: { versions: [], total: 0 } });
        await realApi.getMemoryVersions("m-42");
        expect(calls[0].url).toContain("/v1/memories/m-42/versions");
        expect(calls[0].init.method).toBe("GET");
    });

    test("an empty chain is data, not an error", async () => {
        captureFetch({ body: { versions: [], total: 0 } });
        const res = await realApi.getMemoryVersions("m-1");
        expect(res.versions).toEqual([]);
        expect(res.total).toBe(0);
    });

    test("a 404 rejects, so it can never be mistaken for an empty chain", async () => {
        captureFetch({ status: 404, body: { error: { code: "SM020" } } });
        // require_memory_access answers 404 to a non-member. If this resolved
        // with an empty list instead, the UI would say "never edited".
        await expect(realApi.getMemoryVersions("m-1")).rejects.toMatchObject({
            status: 404,
        });
    });

    test("a 429 carries Retry-After through to the caller", async () => {
        captureFetch({ status: 429, body: {}, headers: { "Retry-After": "30" } });
        await expect(realApi.getMemoryVersions("m-1")).rejects.toMatchObject({
            status: 429,
            retryAfterSeconds: 30,
        });
    });
});

describe("automatic retry is confined to safe reads", () => {
    /**
     * useApiResource retries on its own. That is only acceptable for reads.
     * Rather than assert today's call sites are reads and hope it stays true,
     * derive the set of MUTATING adapter methods from realApi.js itself and
     * assert no page hands one to the hook.
     */
    function mutatingAdapterMethods() {
        const source = fs.readFileSync(path.join(SRC, "lib", "realApi.js"), "utf8");
        const names = new Set();
        // Each entry looks like `name: ... request(..., { method: "POST" ... })`
        const entry = /^\s{4}(\w+):\s*(?:async\s*)?\(/gm;
        let m;
        const starts = [];
        while ((m = entry.exec(source))) starts.push([m[1], m.index]);
        starts.forEach(([name, at], i) => {
            const body = source.slice(at, starts[i + 1]?.[1] ?? source.length);
            if (/method:\s*"(POST|PATCH|PUT|DELETE)"/.test(body)) names.add(name);
        });
        return names;
    }

    test("the adapter really does contain mutating methods (guard is not vacuous)", () => {
        const mutations = mutatingAdapterMethods();
        expect(mutations.size).toBeGreaterThan(0);
        expect(mutations.has("resolveConflict")).toBe(true);
    });

    test("no page passes a mutating method to useApiResource", () => {
        const mutations = mutatingAdapterMethods();
        const pagesDir = path.join(SRC, "pages");
        const offenders = [];

        for (const file of fs.readdirSync(pagesDir).filter((f) => f.endsWith(".jsx"))) {
            const text = fs.readFileSync(path.join(pagesDir, file), "utf8");
            // Every `useApiResource(() => api.something(...)` occurrence.
            const call = /useApiResource\(\s*\(\)\s*=>\s*api\.(\w+)\(/g;
            let m;
            while ((m = call.exec(text))) {
                if (mutations.has(m[1])) offenders.push(`${file}: api.${m[1]}`);
            }
            // Multi-line form, where the arrow body is on the next line.
            const multi = /useApiResource\(\s*\n\s*(?:\/\/[^\n]*\n\s*)*\(\)\s*=>\s*api\.(\w+)\(/g;
            while ((m = multi.exec(text))) {
                if (mutations.has(m[1])) offenders.push(`${file}: api.${m[1]}`);
            }
        }

        expect(offenders).toEqual([]);
    });
});
