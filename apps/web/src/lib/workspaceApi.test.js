/**
 * Workspace endpoints, selection and switch-safety in the real API client.
 *
 * Covers: the three wire contracts (list, create, add member), the error
 * mapper, per-user selection persistence, and the property that matters most
 * when switching workspace: a response that arrives AFTER the switch is
 * dropped, not delivered.
 */

import { realApi, resetIdentityScopedCaches, selectWorkspace, workspaceGeneration, isStaleWorkspaceError } from "./realApi";
import { setTokenGetter } from "./authToken";
import { setCurrentUserId } from "./currentUser";
import { chooseWorkspaceId, readSelection, writeSelection } from "./workspaceSelection";
import { mapWorkspaceError } from "./workspaceErrors";
import { slugify, validateWorkspaceForm } from "./slug";

const WS_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const WS_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const UUID4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const LIST = { data: [{ id: WS_A, name: "A", slug: "a" }, { id: WS_B, name: "B", slug: "b" }] };

const reply = (body, status = 200, headers = {}) => ({
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    headers: { get: (k) => headers[k] ?? null },
    text: async () => JSON.stringify(body),
});

/** Fetch mock: workspace list answered at once, everything else by `handler`. */
function mockFetch(handler) {
    const calls = [];
    global.fetch = jest.fn((url, init) => {
        const u = String(url);
        calls.push({ url: u, init });
        if (/\/v1\/workspaces$/.test(u.split("?")[0]) && (init?.method || "GET") === "GET") {
            return Promise.resolve(reply(LIST));
        }
        return handler ? handler(u, init) : Promise.resolve(reply({}));
    });
    return calls;
}

/** A fetch that stays pending until the test releases it. */
function deferredReply() {
    let release;
    const promise = new Promise((resolve) => { release = resolve; });
    return { promise, release };
}

beforeEach(() => {
    setTokenGetter(null);
    setCurrentUserId("user-1");
    window.localStorage.clear();
    resetIdentityScopedCaches();
});

afterEach(() => {
    delete global.fetch;
    setCurrentUserId(null);
    resetIdentityScopedCaches();
    jest.restoreAllMocks();
});

describe("wire contracts", () => {
    test("listWorkspaces: GET /v1/workspaces, returns the enveloped items", async () => {
        const calls = mockFetch();
        const res = await realApi.listWorkspaces();
        expect(calls[0].url).toMatch(/\/v1\/workspaces$/);
        expect(calls[0].init.method).toBe("GET");
        expect(res.items.map((w) => w.id)).toEqual([WS_A, WS_B]);
    });

    test("createWorkspace: POST with a UUIDv4 Idempotency-Key and the exact body", async () => {
        const calls = mockFetch(() => Promise.resolve(reply({ data: { id: WS_B, name: "Friends", slug: "friends" } }, 201)));
        const created = await realApi.createWorkspace({ name: "Friends", slug: "friends", description: "Five of us" });
        const post = calls[0];
        expect(post.url).toMatch(/\/v1\/workspaces$/);
        expect(post.init.method).toBe("POST");
        expect(post.init.headers["Idempotency-Key"]).toMatch(UUID4);
        expect(JSON.parse(post.init.body)).toEqual({ name: "Friends", slug: "friends", description: "Five of us" });
        expect(created.id).toBe(WS_B);
    });

    test("createWorkspace omits an empty description and mints a new key per call", async () => {
        const calls = mockFetch(() => Promise.resolve(reply({ data: { id: WS_B } }, 201)));
        await realApi.createWorkspace({ name: "One", slug: "one" });
        await realApi.createWorkspace({ name: "Two", slug: "two" });
        expect(JSON.parse(calls[0].init.body)).toEqual({ name: "One", slug: "one" });
        expect(calls[0].init.headers["Idempotency-Key"]).not.toBe(calls[1].init.headers["Idempotency-Key"]);
    });

    test("addWorkspaceMember: POST /members with a UUIDv4 key and only {email, role}", async () => {
        const calls = mockFetch(() => Promise.resolve(reply({ data: { user: { id: "u2" }, role: "viewer", joined_at: "t" } }, 201)));
        const res = await realApi.addWorkspaceMember(WS_A, { email: "friend@example.com", role: "viewer" });
        expect(calls[0].url).toMatch(new RegExp(`/v1/workspaces/${WS_A}/members$`));
        expect(calls[0].init.method).toBe("POST");
        expect(calls[0].init.headers["Idempotency-Key"]).toMatch(UUID4);
        expect(JSON.parse(calls[0].init.body)).toEqual({ email: "friend@example.com", role: "viewer" });
        expect(res.role).toBe("viewer");
    });

    test("addWorkspaceMember defaults the role to member", async () => {
        const calls = mockFetch(() => Promise.resolve(reply({ data: {} }, 201)));
        await realApi.addWorkspaceMember(WS_A, { email: "f@example.com" });
        expect(JSON.parse(calls[0].init.body).role).toBe("member");
    });

    test("a rejected add rejects: nothing resolves as success", async () => {
        mockFetch(() => Promise.resolve(reply(
            { error: { code: "SM023", details: { reason: "user_not_found" } } }, 404)));
        await expect(realApi.addWorkspaceMember(WS_A, { email: "x@example.com" }))
            .rejects.toMatchObject({ status: 404 });
    });
});

describe("error mapping", () => {
    const err = (status, code, reason, extra = {}) => ({
        status,
        body: { error: { code, message: "m", details: reason ? { reason } : {} } },
        ...extra,
    });

    test.each([
        ["user_not_found", err(404, "SM023", "user_not_found"), /sign in to SourceMind once/],
        ["email_not_verified", err(422, "SM010", "email_not_verified"), /not verified/],
        ["email_ambiguous", err(422, "SM010", "email_ambiguous"), /More than one account/],
        ["already_member", err(409, "SM036", "already_member"), /already a member/],
        ["handoff_in_progress", err(409, "SM036", "handoff_in_progress"), /handoff is in progress/],
        ["forbidden", err(403, "SM005"), /owners and admins/],
        ["rate limit", err(429, "SM015", null, { retryAfterSeconds: 30 }), /30 seconds/],
        ["clerk unavailable", err(503, "SM055"), /Nobody was added/],
    ])("add member: %s", (_name, e, text) => {
        expect(mapWorkspaceError(e, "addMember").message).toMatch(text);
    });

    test("create: slug taken is attributed to the slug field", () => {
        const m = mapWorkspaceError(err(409, "SM031"), "create");
        expect(m.field).toBe("slug");
        expect(m.kind).toBe("slug_taken");
    });

    test("create: rate limit and unknown failures map to non-committal copy", () => {
        expect(mapWorkspaceError(err(429, "SM015"), "create").kind).toBe("rate_limit");
        expect(mapWorkspaceError({ status: 500, body: {} }, "create").kind).toBe("generic");
        expect(mapWorkspaceError({ status: 0, network: true }, "addMember").kind).toBe("network");
    });

    test("an error from one action does not borrow another's meaning", () => {
        expect(mapWorkspaceError(err(404, "SM023", "user_not_found"), "create").kind).toBe("generic");
    });
});

describe("slug helpers", () => {
    test("slugify derives a valid URL name", () => {
        expect(slugify("  Our Friends' Team!! ")).toBe("our-friends-team");
        expect(slugify("Café Ünïcode")).toBe("cafe-unicode");
        expect(slugify("---")).toBe("");
    });

    test("validation mirrors the backend rules", () => {
        expect(validateWorkspaceForm({ name: "Friends", slug: "friends" })).toEqual({});
        expect(validateWorkspaceForm({ name: "F", slug: "friends" }).name).toBeTruthy();
        for (const bad of ["a", "-ab", "ab-", "a--b", "AB", "a b", "a_b"]) {
            expect(validateWorkspaceForm({ name: "Friends", slug: bad }).slug).toBeTruthy();
        }
        expect(validateWorkspaceForm({ name: "Friends", slug: "ok", description: "x".repeat(1001) }).description).toBeTruthy();
    });
});

describe("selection persistence", () => {
    test("is stored per user", () => {
        writeSelection("user-1", WS_B);
        expect(readSelection("user-1")).toBe(WS_B);
        expect(readSelection("user-2")).toBeNull();
    });

    test("a valid stored id is used; a stale one is cleared and falls back to list[0]", () => {
        const list = LIST.data;
        writeSelection("user-1", WS_B);
        expect(chooseWorkspaceId(list, "user-1")).toBe(WS_B);

        writeSelection("user-1", "cccccccc-cccc-4ccc-8ccc-cccccccccccc");
        expect(chooseWorkspaceId(list, "user-1")).toBe(WS_A);
        expect(readSelection("user-1")).toBeNull();
    });

    test("localStorage that throws never breaks selection", () => {
        jest.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
        jest.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
        jest.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new Error("blocked"); });
        expect(() => writeSelection("user-1", WS_B)).not.toThrow();
        expect(readSelection("user-1")).toBeNull();
        expect(chooseWorkspaceId(LIST.data, "user-1")).toBe(WS_A);
        expect(chooseWorkspaceId(LIST.data, "user-1", WS_B)).toBe(WS_B);
    });

    test("resolution uses the remembered workspace, not list[0]", async () => {
        writeSelection("user-1", WS_B);
        const calls = mockFetch((u) => Promise.resolve(reply({ conflicts: [], total: 0 })));
        await realApi.listConflicts(undefined, { status: null });
        expect(calls.some((c) => c.url.includes(`/v1/workspaces/${WS_B}/conflicts`))).toBe(true);
    });

    test("a remembered id for another user is ignored", async () => {
        writeSelection("someone-else", WS_B);
        const calls = mockFetch(() => Promise.resolve(reply({ conflicts: [], total: 0 })));
        await realApi.listConflicts(undefined, { status: null });
        expect(calls.some((c) => c.url.includes(`/v1/workspaces/${WS_A}/conflicts`))).toBe(true);
    });

    test("an explicit UUID wins over the remembered one", async () => {
        writeSelection("user-1", WS_B);
        const calls = mockFetch(() => Promise.resolve(reply({ conflicts: [], total: 0 })));
        await realApi.listConflicts(WS_A, { status: null });
        expect(calls.some((c) => c.url.includes(`/v1/workspaces/${WS_A}/conflicts`))).toBe(true);
    });

    test("selectWorkspace redirects later requests and persists for the user", async () => {
        const calls = mockFetch(() => Promise.resolve(reply({ conflicts: [], total: 0 })));
        await realApi.listConflicts(undefined, { status: null });
        selectWorkspace(WS_B);
        await realApi.listConflicts(undefined, { status: null });
        const scoped = calls.filter((c) => c.url.includes("/conflicts")).map((c) => c.url);
        expect(scoped[0]).toContain(WS_A);
        expect(scoped[1]).toContain(WS_B);
        expect(readSelection("user-1")).toBe(WS_B);
    });

    test("selection survives with storage unavailable (in-memory for the session)", async () => {
        jest.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
        jest.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
        const calls = mockFetch(() => Promise.resolve(reply({ conflicts: [], total: 0 })));
        selectWorkspace(WS_B);
        await realApi.listConflicts(undefined, { status: null });
        expect(calls.some((c) => c.url.includes(`/v1/workspaces/${WS_B}/conflicts`))).toBe(true);
    });

    test("a different user does not inherit the in-memory choice", async () => {
        selectWorkspace(WS_B);
        setCurrentUserId("user-2");
        resetIdentityScopedCaches();
        const calls = mockFetch(() => Promise.resolve(reply({ conflicts: [], total: 0 })));
        await realApi.listConflicts(undefined, { status: null });
        expect(calls.some((c) => c.url.includes(`/v1/workspaces/${WS_A}/conflicts`))).toBe(true);
    });
});

describe("a response that arrives after the workspace switch is dropped", () => {
    test("search for A resolving late rejects as stale, and its fetch was aborted", async () => {
        const late = deferredReply();
        const calls = mockFetch((u) => (u.includes("/memories/search") ? late.promise : Promise.resolve(reply({}))));

        const search = realApi.searchMemories({ query: "secret of A" });
        // Let the workspace resolve and the search go out.
        await new Promise((r) => setTimeout(r, 0));
        await new Promise((r) => setTimeout(r, 0));
        const sent = calls.find((c) => c.url.includes("/memories/search"));
        expect(new URL(sent.url, "http://x").searchParams.get("workspace_id")).toBe(WS_A);

        selectWorkspace(WS_B);
        expect(sent.init.signal.aborted).toBe(true);

        // A's answer lands afterwards, carrying A's data.
        late.release(reply({ results: [{ memory: { id: "from-A" } }], total_found: 1 }));
        const outcome = await search.then(() => "resolved", (e) => e);
        expect(isStaleWorkspaceError(outcome)).toBe(true);
    });

    test("a late job-status poll for A is dropped", async () => {
        const late = deferredReply();
        mockFetch((u) => (u.includes("/memories/jobs/") ? late.promise : Promise.resolve(reply({}))));
        const poll = realApi.getJobStatus("job-a");
        await new Promise((r) => setTimeout(r, 0));
        selectWorkspace(WS_B);
        late.release(reply({ data: { status: "completed", memory_ids: ["from-A"] } }));
        expect(isStaleWorkspaceError(await poll.then(() => null, (e) => e))).toBe(true);
    });

    test("a late members fetch for A is dropped and B's is delivered", async () => {
        const lateA = deferredReply();
        mockFetch((u) => {
            if (u.includes(`/v1/workspaces/${WS_A}/members`)) return lateA.promise;
            if (u.includes(`/v1/workspaces/${WS_B}/members`)) return Promise.resolve(reply({ data: [{ user: { id: "b-user" }, role: "owner" }] }));
            return Promise.resolve(reply({}));
        });
        const membersA = realApi.listTeamMembers();
        await new Promise((r) => setTimeout(r, 0));
        await new Promise((r) => setTimeout(r, 0));
        selectWorkspace(WS_B);
        const membersB = await realApi.listTeamMembers();
        lateA.release(reply({ data: [{ user: { id: "a-user" }, role: "owner" }] }));

        expect(isStaleWorkspaceError(await membersA.then(() => null, (e) => e))).toBe(true);
        expect(membersB.members.map((m) => m.user.id)).toEqual(["b-user"]);
    });

    test("a workspace lookup in flight during the switch does not hand out the old id", async () => {
        const lateList = deferredReply();
        global.fetch = jest.fn(() => lateList.promise);
        const pending = realApi.getWorkspace();
        await new Promise((r) => setTimeout(r, 0));
        selectWorkspace(WS_B);
        lateList.release(reply(LIST));
        expect(isStaleWorkspaceError(await pending.then(() => null, (e) => e))).toBe(true);
    });

    test("requests about the user rather than a workspace survive a switch", async () => {
        const late = deferredReply();
        mockFetch((u) => (u.includes("/team/me") ? late.promise : Promise.resolve(reply({}))));
        const me = realApi.getCurrentUser();
        await new Promise((r) => setTimeout(r, 0));
        selectWorkspace(WS_B);
        late.release(reply({ data: { id: "u1", email: "me@example.com" } }));
        expect((await me).email).toBe("me@example.com");
    });

    test("a request started after the switch is not stale", async () => {
        mockFetch((u) => Promise.resolve(reply({ conflicts: [], total: 0 })));
        selectWorkspace(WS_B);
        const before = workspaceGeneration();
        await expect(realApi.listConflicts(undefined, { status: null })).resolves.toBeTruthy();
        expect(workspaceGeneration()).toBe(before);
    });
});


describe("review follow-ups: lookup ownership, aborted bodies, error copy, mock parity", () => {
    test("a lookup for user A finishing after user B signs in leaves B's saved choice intact", async () => {
        const lateList = deferred2();
        global.fetch = jest.fn(() => lateList.promise);
        setCurrentUserId("user-A");
        const pending = realApi.getWorkspace();
        await new Promise((r) => setTimeout(r, 0));

        // B signs in (as the Clerk bridge does: publish id, then reset) and has a saved choice.
        setCurrentUserId("user-B");
        writeSelection("user-B", WS_B);
        resetIdentityScopedCaches();

        // A's list (which does NOT contain B's workspace) arrives afterwards.
        lateList.release(reply({ data: [{ id: WS_A, name: "A", slug: "a" }] }));
        expect(isStaleWorkspaceError(await pending.then(() => null, (e) => e))).toBe(true);
        expect(readSelection("user-B")).toBe(WS_B);
    });

    test("an abort while the body is being read is the stale result, not a failure", async () => {
        const res = {
            ok: true, status: 200, statusText: "OK", headers: { get: () => null },
            text: () => new Promise((_resolve, reject) => {
                // Rejects when the scope changes, like a real aborted body read.
                setTimeout(() => reject(Object.assign(new Error("aborted"), { name: "AbortError" })), 20);
            }),
        };
        global.fetch = jest.fn((u) => (/\/v1\/workspaces$/.test(String(u).split("?")[0])
            ? Promise.resolve(reply(LIST)) : Promise.resolve(res)));
        const search = realApi.searchMemories({ query: "q" });
        await new Promise((r) => setTimeout(r, 0));
        await new Promise((r) => setTimeout(r, 0));
        selectWorkspace(WS_B);
        expect(isStaleWorkspaceError(await search.then(() => null, (e) => e))).toBe(true);
    });

    test("a genuine body-read failure stays an ordinary error", async () => {
        const res = {
            ok: true, status: 200, statusText: "OK", headers: { get: () => null },
            text: () => Promise.reject(new TypeError("connection reset")),
        };
        global.fetch = jest.fn((u) => (/\/v1\/workspaces$/.test(String(u).split("?")[0])
            ? Promise.resolve(reply(LIST)) : Promise.resolve(res)));
        const err = await realApi.searchMemories({ query: "q" }).then(() => null, (e) => e);
        expect(isStaleWorkspaceError(err)).toBe(false);
        expect(err).toBeInstanceOf(TypeError);
    });

    test("add member against a server without the route says so", () => {
        for (const e of [{ status: 405, body: { detail: "Method Not Allowed" } }, { status: 404, body: { detail: "Not Found" } }]) {
            const m = mapWorkspaceError(e, "addMember");
            expect(m.kind).toBe("unsupported");
            expect(m.message).toMatch(/does not support adding members yet/);
        }
        // A coded 404 is still the real "no such user".
        expect(mapWorkspaceError({ status: 404, body: { error: { code: "SM023", details: { reason: "user_not_found" } } } }, "addMember").kind)
            .toBe("user_not_found");
    });

    test("mock mode: created workspace is what getWorkspace returns once selected; demo owner is a member", async () => {
        const { mockApi } = require("./mockApi");
        const { CURRENT_USER } = require("./mockData");
        const created = await mockApi.createWorkspace({ name: "Friends Mock", slug: "friends-mock" });
        selectWorkspace(created.id);
        const ws = await mockApi.getWorkspace();
        expect(ws.slug).toBe("friends-mock");
        const { members } = await mockApi.listTeamMembers();
        const mine = members.find((m) => (m.user || m).id === CURRENT_USER.id);
        expect(String(mine.role).toLowerCase()).toBe("owner");
    });
});

function deferred2() {
    let release;
    const promise = new Promise((resolve) => { release = resolve; });
    return { promise, release };
}