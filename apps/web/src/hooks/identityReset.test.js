/**
 * What happens on screen when the signed-in user changes.
 *
 * Clearing `_wsPromise` was the whole of the previous fix, and it only governs
 * workspace lookups that have not started yet. It says nothing about the two
 * things a user actually sees:
 *
 *   - rows already rendered, which still belong to the previous user;
 *   - requests already on the wire, issued under the previous user's token and
 *     scoped to their workspace, which resolve into the new user's screen.
 *
 * The second is the one that matters. The API is not fooled — the isolation
 * gate answers 404 to a non-member — but this failure never reaches the API.
 * A's read was authorized when it was issued; it simply lands late.
 */

import React from "react";
import { render, screen, act, waitFor } from "@testing-library/react";

import useApiResource from "./useApiResource";
import { resetIdentityScopedCaches, onIdentityReset, identityGeneration } from "../lib/realApi";

/** Renders whatever the hook currently holds, so assertions are about pixels. */
function Screen({ fetcher }) {
    const { data, loading, error } = useApiResource(fetcher, { maxAutoRetries: 0 });
    return (
        <div>
            <span data-testid="rows">{data ? data.join(",") : ""}</span>
            <span data-testid="loading">{loading ? "yes" : "no"}</span>
            <span data-testid="error">{error ? error.kind : ""}</span>
        </div>
    );
}

const rows = () => screen.getByTestId("rows").textContent;

/**
 * Let queued microtasks run inside act().
 *
 * useApiResource issues its fetch from `Promise.resolve().then(...)`, so
 * nothing is on the wire on the render tick itself. Asserting immediately
 * after render() measures the harness, not the hook.
 */
const flush = () => act(async () => {});

afterEach(() => {
    jest.restoreAllMocks();
});

describe("the identity registry itself", () => {
    test("a reset bumps the generation and notifies subscribers", () => {
        const seen = [];
        const unsubscribe = onIdentityReset((g) => seen.push(g));
        const before = identityGeneration();

        resetIdentityScopedCaches();

        expect(identityGeneration()).toBe(before + 1);
        expect(seen).toEqual([before + 1]);
        unsubscribe();
    });

    test("an unsubscribed listener stops being called", () => {
        const fn = jest.fn();
        onIdentityReset(fn)();
        resetIdentityScopedCaches();
        expect(fn).not.toHaveBeenCalled();
    });

    test("one broken subscriber does not stop the others being told", () => {
        const good = jest.fn();
        const offBad = onIdentityReset(() => { throw new Error("subscriber blew up"); });
        const offGood = onIdentityReset(good);

        expect(() => resetIdentityScopedCaches()).not.toThrow();
        expect(good).toHaveBeenCalledTimes(1);

        offBad();
        offGood();
    });
});

describe("rendered data", () => {
    test("A's rows leave the screen the moment the user changes", async () => {
        let resolveNext;
        const fetcher = jest.fn(() => new Promise((r) => { resolveNext = r; }));

        render(<Screen fetcher={fetcher} />);
        await flush();
        await act(async () => { resolveNext(["A-row-1", "A-row-2"]); });
        expect(rows()).toBe("A-row-1,A-row-2");

        // B signs in without a document reload.
        await act(async () => { resetIdentityScopedCaches(); });

        // Not "eventually, once B's data arrives" — immediately, and B's own
        // request has not resolved yet.
        expect(rows()).toBe("");
        expect(screen.getByTestId("loading").textContent).toBe("yes");
    });

    test("the data is re-fetched under the new identity", async () => {
        let resolveNext;
        const fetcher = jest.fn(() => new Promise((r) => { resolveNext = r; }));

        render(<Screen fetcher={fetcher} />);
        await flush();
        await act(async () => { resolveNext(["A-row"]); });
        expect(fetcher).toHaveBeenCalledTimes(1);

        await act(async () => { resetIdentityScopedCaches(); });
        await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));

        // `resolveNext` now refers to the SECOND call's resolver: the fetcher
        // reassigns it each time it is invoked.
        await act(async () => { resolveNext(["B-row"]); });
        expect(rows()).toBe("B-row");
    });
});

describe("pending responses", () => {
    test("A's in-flight read does NOT land on B's screen", async () => {
        // The dangerous ordering: A's request is slow, B signs in, then A's
        // response — authorized when it was issued — finally arrives.
        const pending = [];
        const fetcher = jest.fn(() => new Promise((r) => pending.push(r)));

        render(<Screen fetcher={fetcher} />);
        await flush();
        expect(pending).toHaveLength(1);       // A's request, still on the wire

        await act(async () => { resetIdentityScopedCaches(); });
        await waitFor(() => expect(pending).toHaveLength(2));   // B's request

        // A's response arrives last.
        await act(async () => { pending[0](["A-private-row"]); });

        expect(rows()).toBe("");
        expect(rows()).not.toContain("A-private-row");

        // B's own response is still accepted afterwards.
        await act(async () => { pending[1](["B-row"]); });
        expect(rows()).toBe("B-row");
    });

    test("a late REJECTION from A does not raise an error on B's screen", async () => {
        // A's token is gone, so A's in-flight read typically ends in 401.
        // Rendering that would tell B they are signed out when they are not.
        const pending = [];
        const fetcher = jest.fn(
            () => new Promise((resolve, reject) => pending.push({ resolve, reject }))
        );

        render(<Screen fetcher={fetcher} />);
        await flush();
        await act(async () => { resetIdentityScopedCaches(); });
        await waitFor(() => expect(pending).toHaveLength(2));

        await act(async () => {
            pending[0].reject({ status: 401, body: { error: { code: "SM001" } } });
        });

        expect(screen.getByTestId("error").textContent).toBe("");
    });
});
