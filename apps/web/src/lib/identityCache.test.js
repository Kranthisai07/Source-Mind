/**
 * Regression: identity-scoped caches must not survive a user change.
 *
 * `_wsPromise` in realApi.js memoises the workspace UUID for the module's
 * lifetime. That is right for one signed-in user and wrong the moment the user
 * changes — B would inherit A's workspace id, and every scoped request would
 * be issued against a workspace B may not belong to. The isolation gate would
 * answer 404, so no data leaks, but the UI would be asking the wrong question
 * and would report "not available" instead of showing B their own workspace.
 *
 * Reachability today: a sign-out is a full document navigation, because
 * ClerkProvider is configured without routerPush/routerReplace and Clerk then
 * navigates via window.location. Module state is discarded wholesale, so the
 * stale-cache path cannot be reached. These tests exist because that is an
 * incidental property of Clerk's configuration rather than something the app
 * asserts: adding router props — the ordinary Clerk + React Router
 * integration — makes sign-out an SPA navigation and the cache would then
 * survive.
 */

import { realApi, resetIdentityScopedCaches } from "./realApi";
import { setTokenGetter } from "./authToken";

/**
 * Route by URL rather than by call order: the workspace lookup happens a
 * variable number of times (that is the property under test), so an
 * order-indexed mock would answer the wrong body after a reset.
 */
function fetchRouting(workspaceBody) {
    const calls = [];
    global.fetch = jest.fn(async (url) => {
        const u = String(url);
        calls.push(u);
        const body = u.includes("/v1/workspaces") && !u.includes("/conflicts")
            ? workspaceBody
            : { conflicts: [], total: 0 };
        return {
            ok: true,
            status: 200,
            statusText: "OK",
            headers: { get: () => null },
            text: async () => JSON.stringify(body),
        };
    });
    return calls;
}

const WS_A = { data: [{ id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", name: "A" }] };
const WS_B = { data: [{ id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", name: "B" }] };

beforeEach(() => {
    setTokenGetter(null);
    resetIdentityScopedCaches();
});

afterEach(() => {
    delete global.fetch;
    resetIdentityScopedCaches();
    jest.restoreAllMocks();
});

describe("workspace memoisation", () => {
    test("is shared within one session — the lookup happens once", async () => {
        const calls = fetchRouting(WS_A);

        await realApi.listConflicts(undefined, { status: null });
        await realApi.listConflicts(undefined, { status: null });

        const lookups = calls.filter((u) => /\/v1\/workspaces$/.test(u.split("?")[0]));
        // Memoised on purpose: concurrent callers on first paint share one
        // request instead of each firing their own.
        expect(lookups).toHaveLength(1);
    });
});

describe("resetIdentityScopedCaches", () => {
    test("forces the workspace to be resolved again", async () => {
        const calls = fetchRouting(WS_A);
        await realApi.listConflicts(undefined, { status: null });
        expect(calls.filter((u) => /\/v1\/workspaces$/.test(u.split("?")[0]))).toHaveLength(1);

        // User changed.
        resetIdentityScopedCaches();

        await realApi.listConflicts(undefined, { status: null });
        expect(calls.filter((u) => /\/v1\/workspaces$/.test(u.split("?")[0]))).toHaveLength(2);
    });

    test("the second user's requests are scoped to the SECOND workspace", async () => {
        // A signs in: their workspace is resolved and memoised.
        let calls = fetchRouting(WS_A);
        await realApi.listConflicts(undefined, { status: null });
        const scopedForA = calls.find((u) => u.includes("/conflicts"));
        expect(scopedForA).toContain("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");

        // B signs in without a reload. Without the reset, B would keep asking
        // about A's workspace.
        resetIdentityScopedCaches();

        calls = fetchRouting(WS_B);
        await realApi.listConflicts(undefined, { status: null });
        const scopedForB = calls.find((u) => u.includes("/conflicts"));

        expect(scopedForB).toContain("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb");
        expect(scopedForB).not.toContain("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
    });

    test("without the reset, the stale workspace persists — the bug this prevents", async () => {
        let calls = fetchRouting(WS_A);
        await realApi.listConflicts(undefined, { status: null });

        // Deliberately NOT resetting, to show the failure mode is real.
        calls = fetchRouting(WS_B);
        await realApi.listConflicts(undefined, { status: null });

        const scoped = calls.find((u) => u.includes("/conflicts"));
        expect(scoped).toContain("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
        // B's workspace was never even looked up.
        expect(calls.filter((u) => /\/v1\/workspaces$/.test(u.split("?")[0]))).toHaveLength(0);
    });
});
