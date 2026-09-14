/**
 * Stale-response handling.
 *
 * The failure this prevents: workspace A is loading, the workspace changes to
 * B, B resolves quickly, then A's slower response lands and repopulates the
 * screen with data from a workspace the user is no longer in. The same shape
 * occurs when access is revoked mid-flight — an already-authorized read
 * completes and refills a screen that should now be empty.
 *
 * The old `let cancelled = false` flag in each effect does not cover this. It
 * handles unmount, but a fourth request superseding a second happens inside one
 * effect instance, where nothing is cancelled.
 */

import { createRequestGate } from "./requestGate";

describe("request gate", () => {
    test("accepts the only in-flight request", () => {
        const gate = createRequestGate();
        const token = gate.begin();
        expect(gate.accept(token)).toBe(true);
    });

    test("a slow earlier response is rejected once a newer request starts", () => {
        const gate = createRequestGate();
        const first = gate.begin();   // workspace A
        const second = gate.begin();  // workspace B supersedes it

        // B lands first and is accepted.
        expect(gate.accept(second)).toBe(true);
        // A finally lands. It must not touch state.
        expect(gate.accept(first)).toBe(false);
    });

    test("out-of-order completion still admits only the newest", () => {
        const gate = createRequestGate();
        const a = gate.begin();
        const b = gate.begin();
        const c = gate.begin();
        expect(gate.accept(a)).toBe(false);
        expect(gate.accept(b)).toBe(false);
        expect(gate.accept(c)).toBe(true);
    });

    test("invalidate() drops in-flight work without starting a request", () => {
        const gate = createRequestGate();
        const inFlight = gate.begin();

        // Workspace changed, or access was lost. Nothing new is requested, but
        // whatever is already on the wire must not be allowed to land.
        gate.invalidate();

        expect(gate.accept(inFlight)).toBe(false);
    });

    test("close() rejects everything, including the current request", () => {
        const gate = createRequestGate();
        const token = gate.begin();
        gate.close();
        expect(gate.accept(token)).toBe(false);
        expect(gate.isClosed).toBe(true);
    });

    test("a token from before a close is never revived by a later begin()", () => {
        const gate = createRequestGate();
        const old = gate.begin();
        gate.close();
        gate.begin();
        expect(gate.accept(old)).toBe(false);
    });

    test("integration: A in flight, switch to B, A resolves last", async () => {
        const gate = createRequestGate();
        const applied = [];

        const slowA = new Promise((r) => setTimeout(() => r("A-data"), 20));
        const fastB = Promise.resolve("B-data");

        const tokenA = gate.begin();
        const pendingA = slowA.then((v) => {
            if (gate.accept(tokenA)) applied.push(v);
        });

        // The workspace changes before A comes back.
        const tokenB = gate.begin();
        const pendingB = fastB.then((v) => {
            if (gate.accept(tokenB)) applied.push(v);
        });

        await Promise.all([pendingA, pendingB]);

        // Only B's data was ever applied; A's arrived late and was discarded.
        expect(applied).toEqual(["B-data"]);
    });
});
