/**
 * "Try again" after a failed search.
 *
 * The defect: the retry handler was `() => setQuery((q) => q)`. Setting state
 * to a value React considers identical is a no-op — React bails out of the
 * re-render, so the `[query, mode]` effect never re-runs and no request is
 * issued. The button looked like a recovery affordance and did nothing at all.
 *
 * This is a component-level test on purpose. Nothing at the unit level could
 * have caught it: the handler is wired correctly, the effect is written
 * correctly, and the API adapter is fine. The bug lives in the interaction
 * between a state update that changes nothing and an effect keyed on that
 * state, which only exists once React is rendering.
 *
 * What the fix must preserve: the retry has to reissue the SAME query and the
 * SAME mode. Recovering by silently searching for something else would be
 * worse than not recovering.
 */

import React from "react";
import { render, screen, act, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import Memories from "./Memories";
import api from "../lib/api";

function renderPage() {
    return render(
        <MemoryRouter>
            <Memories />
        </MemoryRouter>
    );
}

/** The page debounces by 120ms before searching. */
const settle = async () => {
    await act(async () => {
        await new Promise((r) => setTimeout(r, 200));
    });
};

let search;

beforeEach(() => {
    search = jest.spyOn(api, "searchMemories");
});

afterEach(() => {
    jest.restoreAllMocks();
});

describe("recovering from a failed search", () => {
    test("Try again issues a NEW request", async () => {
        search.mockRejectedValue({ status: 503, body: null });

        renderPage();
        await settle();

        await waitFor(() => expect(screen.getByTestId("error-state")).toBeTruthy());
        const callsBeforeRetry = search.mock.calls.length;
        expect(callsBeforeRetry).toBeGreaterThan(0);

        await act(async () => {
            fireEvent.click(screen.getByTestId("error-retry"));
        });
        await settle();

        // Before the fix this is equal: the click changed no state React could
        // see, so the effect never re-ran and nothing was requested.
        expect(search.mock.calls.length).toBeGreaterThan(callsBeforeRetry);
    });

    test("the retry reuses the same query and mode", async () => {
        search.mockRejectedValue({ status: 503, body: null });

        renderPage();
        await settle();

        // Type a query and pick a non-default mode, then let it fail.
        await act(async () => {
            fireEvent.change(screen.getByTestId("memories-search-input"), {
                target: { value: "vector index" },
            });
        });
        await act(async () => {
            fireEvent.click(screen.getByTestId("mode-semantic"));
        });
        await settle();
        await waitFor(() => expect(screen.getByTestId("error-state")).toBeTruthy());

        const before = search.mock.calls.length;
        await act(async () => {
            fireEvent.click(screen.getByTestId("error-retry"));
        });
        await settle();

        expect(search.mock.calls.length).toBeGreaterThan(before);
        const last = search.mock.calls[search.mock.calls.length - 1][0];
        expect(last.query).toBe("vector index");
        expect(last.mode).toBe("semantic");
    });

    test("a retry that succeeds clears the error and shows the results", async () => {
        search.mockRejectedValueOnce({ status: 503, body: null });

        renderPage();
        await settle();
        await waitFor(() => expect(screen.getByTestId("error-state")).toBeTruthy());

        // The recovery path: the next attempt works.
        search.mockResolvedValue({
            results: [
                {
                    id: "m-1",
                    content: "Vector index is HNSW.",
                    tags: ["index"],
                    importance_score: 0.9,
                    created_at: "2026-09-01T00:00:00Z",
                },
            ],
            total_found: 1,
            latency_ms: 12,
        });

        await act(async () => {
            fireEvent.click(screen.getByTestId("error-retry"));
        });
        await settle();

        await waitFor(() =>
            expect(screen.queryByTestId("error-state")).toBeNull()
        );
        expect(screen.getByText(/Vector index is HNSW/)).toBeTruthy();
    });
});
