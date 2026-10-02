/**
 * Blank queries on the Memories page.
 *
 * THE DEFECT
 *
 * On load `query` is "", and the effect called searchMemories unconditionally,
 * so every visit POSTed {"query": "", ...} to /v1/memories/search. The backend
 * declares `SearchRequest.query` with min_length=1 and answers 422 SM010
 * ("String should have at least 1 character"), so the page opened on
 * "Something went wrong". There is no list-all-memories endpoint to fall back
 * on; a blank query simply has nothing to ask the API.
 *
 * WHAT THESE ASSERT
 *
 * A blank or whitespace-only query never reaches the API, the page shows
 * "Type to search memories" instead of an error, and clearing the box
 * invalidates any search still in flight so a late answer cannot overwrite
 * that state. Non-empty searching, and the Ingest button, are unchanged.
 * The API is mocked; no network, provider or production call is made.
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

const type = async (value) => {
    await act(async () => {
        fireEvent.change(screen.getByTestId("memories-search-input"), {
            target: { value },
        });
    });
};

/** A promise the test resolves or rejects by hand, to model a slow response. */
const deferred = () => {
    let resolve, reject;
    const promise = new Promise((res, rej) => {
        resolve = res;
        reject = rej;
    });
    return { promise, resolve, reject };
};

const HIT = {
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
};

let search;

beforeEach(() => {
    search = jest.spyOn(api, "searchMemories");
});

afterEach(() => {
    jest.restoreAllMocks();
});

describe("blank queries never reach the API", () => {
    test("the initial blank query makes no request and shows the prompt", async () => {
        search.mockResolvedValue(HIT);

        renderPage();
        await settle();

        expect(search).not.toHaveBeenCalled();
        expect(screen.getByText("Type to search memories")).toBeTruthy();
        expect(screen.queryByTestId("error-state")).toBeNull();
    });

    test("a whitespace-only query makes no request and shows the prompt", async () => {
        search.mockResolvedValue(HIT);

        renderPage();
        await settle();
        await type("   \t ");
        await settle();

        expect(search).not.toHaveBeenCalled();
        expect(screen.getByText("Type to search memories")).toBeTruthy();
        expect(screen.queryByTestId("error-state")).toBeNull();
    });

    test("changing the mode with a blank query still makes no request", async () => {
        search.mockResolvedValue(HIT);

        renderPage();
        await settle();
        await act(async () => {
            fireEvent.click(screen.getByTestId("mode-semantic"));
        });
        await settle();

        expect(search).not.toHaveBeenCalled();
        expect(screen.getByText("Type to search memories")).toBeTruthy();
    });
});

describe("non-empty queries search as before", () => {
    test("typing a query searches with that query and renders the results", async () => {
        search.mockResolvedValue(HIT);

        renderPage();
        await settle();
        await type("vector index");
        await settle();

        expect(search).toHaveBeenCalledTimes(1);
        expect(search.mock.calls[0][0]).toEqual({
            query: "vector index",
            mode: "hybrid",
            limit: 24,
        });
        await waitFor(() =>
            expect(screen.getByText(/Vector index is HNSW/)).toBeTruthy()
        );
        expect(screen.queryByText("Type to search memories")).toBeNull();
    });

    test("a failed non-empty search still shows the error state", async () => {
        search.mockRejectedValue({ status: 503, body: null });

        renderPage();
        await settle();
        await type("vector index");
        await settle();

        await waitFor(() => expect(screen.getByTestId("error-state")).toBeTruthy());
    });
});

describe("clearing the query invalidates an in-flight search", () => {
    test("a late success after clearing does not replace the prompt", async () => {
        const slow = deferred();
        search.mockReturnValue(slow.promise);

        renderPage();
        await settle();
        await type("vector index");
        await settle();
        expect(search).toHaveBeenCalledTimes(1); // request is now pending

        await type("");
        await settle();
        expect(screen.getByText("Type to search memories")).toBeTruthy();

        await act(async () => {
            slow.resolve(HIT);
        });
        await settle();

        expect(screen.queryByText(/Vector index is HNSW/)).toBeNull();
        expect(screen.queryByTestId("results-meta")).toBeNull();
        expect(screen.getByText("Type to search memories")).toBeTruthy();
        expect(search).toHaveBeenCalledTimes(1);
    });

    test("a late failure after clearing does not raise an error", async () => {
        const slow = deferred();
        search.mockReturnValue(slow.promise);

        renderPage();
        await settle();
        await type("vector index");
        await settle();
        expect(search).toHaveBeenCalledTimes(1);

        await type("   ");
        await settle();

        await act(async () => {
            slow.reject({ status: 503, body: null });
        });
        await settle();

        expect(screen.queryByTestId("error-state")).toBeNull();
        expect(screen.getByText("Type to search memories")).toBeTruthy();
    });

    test("clearing after an error removes the error and shows the prompt", async () => {
        search.mockRejectedValue({ status: 503, body: null });

        renderPage();
        await settle();
        await type("vector index");
        await settle();
        await waitFor(() => expect(screen.getByTestId("error-state")).toBeTruthy());

        await type("");
        await settle();

        expect(screen.queryByTestId("error-state")).toBeNull();
        expect(screen.getByText("Type to search memories")).toBeTruthy();
    });
});

describe("the Ingest button stays available", () => {
    test("with a blank query", async () => {
        renderPage();
        await settle();

        const btn = screen.getByTestId("ingest-open-btn");
        expect(btn).toBeTruthy();
        expect(btn.disabled).toBe(false);
    });

    test("after a search was cleared, and it still opens the panel", async () => {
        search.mockResolvedValue(HIT);

        renderPage();
        await settle();
        await type("vector index");
        await settle();
        await type("");
        await settle();

        const btn = screen.getByTestId("ingest-open-btn");
        expect(btn.disabled).toBe(false);
        await act(async () => {
            fireEvent.click(btn);
        });
        await waitFor(() => expect(screen.getByTestId("ingest-panel")).toBeTruthy());
    });
});
