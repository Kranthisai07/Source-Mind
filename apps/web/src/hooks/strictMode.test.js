/**
 * The hook under React Strict Mode.
 *
 * Why this file exists separately from requestGate.test.js: the gate's ordering
 * rules were already tested, thoroughly, and they were all correct. The defect
 * was never in the gate. It was in how the hook wires the gate to React's
 * lifecycle — and a pure unit test of the gate cannot see that wiring, because
 * the wiring is the part React owns.
 *
 * Strict Mode deliberately runs every effect twice in development:
 * setup → cleanup → setup. It does that to surface exactly this class of bug,
 * where cleanup destroys something that setup does not rebuild.
 *
 * `gate` lives in a `useRef`, so the SAME gate object survives the simulated
 * unmount. The cleanup calls `close()`, which sets `closed = true`
 * permanently. After the second setup the hook issues a request against a gate
 * that can never accept anything again, so `accept()` returns false forever
 * and the screen stays on its skeleton with data that arrived and was thrown
 * away.
 *
 * The fix must keep BOTH properties the gate exists for — stale responses
 * rejected, and responses after a real unmount rejected — while surviving
 * setup/cleanup/setup. Disabling Strict Mode is not a fix; it is hiding the
 * report.
 */

import React, { StrictMode } from "react";
import { render, screen, act, waitFor } from "@testing-library/react";

import useApiResource from "./useApiResource";

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
const flush = () => act(async () => {});

afterEach(() => jest.restoreAllMocks());

describe("Strict Mode", () => {
    test("data still renders after the double setup/cleanup/setup", async () => {
        let resolveNext;
        const fetcher = jest.fn(() => new Promise((r) => { resolveNext = r; }));

        render(
            <StrictMode>
                <Screen fetcher={fetcher} />
            </StrictMode>
        );
        await flush();

        await act(async () => { resolveNext(["row-1", "row-2"]); });

        // Before the fix this is "" forever: the response arrived, the gate had
        // been permanently closed by the simulated unmount, and it was dropped.
        expect(rows()).toBe("row-1,row-2");
        expect(screen.getByTestId("loading").textContent).toBe("no");
    });

    test("an error still surfaces after the double invoke", async () => {
        let rejectNext;
        const fetcher = jest.fn(
            () => new Promise((_, reject) => { rejectNext = reject; })
        );

        render(
            <StrictMode>
                <Screen fetcher={fetcher} />
            </StrictMode>
        );
        await flush();
        await act(async () => {
            rejectNext({ status: 503, body: null });
        });

        // A permanently closed gate swallows failures too, leaving a skeleton
        // that never resolves either way.
        expect(screen.getByTestId("error").textContent).toBe("server");
        expect(screen.getByTestId("loading").textContent).toBe("no");
    });

    test("a stale response is STILL rejected under Strict Mode", async () => {
        // The fix must not restore liveness by weakening the gate. A response
        // superseded by a newer request must still be dropped.
        const pending = [];
        const fetcher = jest.fn(() => new Promise((r) => pending.push(r)));

        const { rerender } = render(
            <StrictMode>
                <Screen fetcher={fetcher} key="a" />
            </StrictMode>
        );
        await flush();
        const firstCount = pending.length;

        // Force a second request from the same mounted hook.
        rerender(
            <StrictMode>
                <Screen fetcher={fetcher} key="a" />
            </StrictMode>
        );
        await flush();

        // Resolve the OLDEST request last; it must not win.
        await act(async () => { pending[pending.length - 1](["newest"]); });
        await act(async () => { pending[0](["oldest"]); });

        expect(rows()).toBe("newest");
        expect(rows()).not.toContain("oldest");
        expect(pending.length).toBeGreaterThanOrEqual(firstCount);
    });

    test("a response after a REAL unmount is still rejected", async () => {
        // The other property the gate exists for. Losing this would trade one
        // bug for a React state-update-after-unmount warning, and for writes
        // into a component nobody is looking at.
        const pending = [];
        const fetcher = jest.fn(() => new Promise((r) => pending.push(r)));

        const { unmount } = render(
            <StrictMode>
                <Screen fetcher={fetcher} />
            </StrictMode>
        );
        await flush();

        const errors = jest.spyOn(console, "error").mockImplementation(() => {});
        unmount();
        await act(async () => { pending.forEach((r) => r(["after-unmount"])); });

        const complaints = errors.mock.calls
            .map((c) => String(c[0]))
            .filter((m) => /unmounted|not wrapped in act/i.test(m));
        expect(complaints).toEqual([]);
    });
});
