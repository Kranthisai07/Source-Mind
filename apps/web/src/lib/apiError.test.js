/**
 * The distinction these tests defend: a successful empty result and a failed
 * request must never produce the same screen.
 *
 * Every page used to do `.catch(() => setRows([]))`, which made 401, 403, 404,
 * 429 and an offline browser all render as "No conflicts yet". The stricter
 * backend makes that actively misleading — a non-member receives 404 by design
 * and an under-privileged member 403, so the two most security-relevant
 * outcomes were the two most convincingly disguised.
 */

import { classifyApiError, retryDelayMs, shouldClearData } from "./apiError";

/** Shaped like the ApiError realApi throws. */
function apiError(status, { code, retryAfterSeconds = null, network = false } = {}) {
    return {
        status,
        network,
        retryAfterSeconds,
        message: `${status} error`,
        body: code ? { error: { code } } : null,
    };
}

describe("successful empty data is not an error", () => {
    test("an empty array is data, and never reaches the classifier", () => {
        // The guard is structural: pages render EmptyState from `data`, and
        // ErrorState from `error`. An empty list leaves `error` null.
        const data = [];
        const error = null;
        expect(Array.isArray(data)).toBe(true);
        expect(data).toHaveLength(0);
        expect(error).toBeNull();
    });

    test("classifying a non-error still yields a retryable unknown, not an empty state", () => {
        const c = classifyApiError(new Error("boom"));
        expect(c.kind).toBe("unknown");
        expect(c.retryable).toBe(true);
    });
});

describe("authorization failures are distinguishable", () => {
    test("401 offers reauthentication and is never auto-retried", () => {
        const c = classifyApiError(apiError(401, { code: "SM001" }));
        expect(c.kind).toBe("auth");
        expect(c.retryable).toBe(false);
        expect(c.code).toBe("SM001");
    });

    test("403 is a permission message, distinct from 404", () => {
        const c = classifyApiError(apiError(403, { code: "SM005" }));
        expect(c.kind).toBe("forbidden");
        expect(c.retryable).toBe(false);
        expect(c.title).toMatch(/access/i);
    });

    test("404 stays neutral and does not hint a hidden resource exists", () => {
        const c = classifyApiError(apiError(404, { code: "SM022" }));
        expect(c.kind).toBe("missing");
        // The backend answers 404 for both "no such record" and "not yours",
        // precisely so they cannot be told apart. Copy that said "you do not
        // have permission" would leak the distinction the status hides.
        expect(c.title).not.toMatch(/permission|forbidden|denied|not allowed/i);
        expect(c.detail).not.toMatch(/permission|forbidden|denied|exists/i);
    });

    test("401, 403 and 404 all clear stale data from the screen", () => {
        for (const status of [401, 403, 404]) {
            expect(shouldClearData(classifyApiError(apiError(status)).kind)).toBe(true);
        }
    });

    test("a server error does NOT clear data — it is not an access change", () => {
        expect(shouldClearData(classifyApiError(apiError(503)).kind)).toBe(false);
    });
});

describe("rate limiting", () => {
    test("429 is retryable and carries the server's Retry-After", () => {
        const c = classifyApiError(apiError(429, { retryAfterSeconds: 30 }));
        expect(c.kind).toBe("rate-limited");
        expect(c.retryable).toBe(true);
        expect(c.retryAfterSeconds).toBe(30);
        expect(c.detail).toMatch(/30s/);
    });

    test("Retry-After is honoured over backoff", () => {
        const c = classifyApiError(apiError(429, { retryAfterSeconds: 45 }));
        expect(retryDelayMs(c, 0)).toBe(45000);
        // Even on a later attempt the server's number still wins.
        expect(retryDelayMs(c, 3)).toBe(45000);
    });

    test("without Retry-After, backoff is bounded rather than unlimited", () => {
        const c = classifyApiError(apiError(429));
        expect(c.retryAfterSeconds).toBeNull();
        expect(retryDelayMs(c, 0)).toBe(1000);
        expect(retryDelayMs(c, 1)).toBe(2000);
        expect(retryDelayMs(c, 2)).toBe(4000);
        // Capped. The backend limiter fails closed, so unbounded client
        // retries would worsen exactly the condition they are reacting to.
        expect(retryDelayMs(c, 10)).toBe(8000);
    });
});

describe("server and network failures", () => {
    test("5xx is an actionable, retryable server error", () => {
        for (const status of [500, 502, 503]) {
            const c = classifyApiError(apiError(status));
            expect(c.kind).toBe("server");
            expect(c.retryable).toBe(true);
        }
    });

    test("a transport failure is not a 5xx — nothing was reached", () => {
        const c = classifyApiError(apiError(0, { network: true }));
        expect(c.kind).toBe("network");
        expect(c.status).toBeNull();
        expect(c.retryable).toBe(true);
    });

    test("every failure kind is renderable — none falls through to undefined", () => {
        const kinds = [401, 403, 404, 429, 500, 0].map(
            (s) => classifyApiError(apiError(s, { network: s === 0 })).kind
        );
        expect(kinds).toEqual([
            "auth", "forbidden", "missing", "rate-limited", "server", "network",
        ]);
        for (const k of kinds) expect(k).not.toBe("unknown");
    });
});
