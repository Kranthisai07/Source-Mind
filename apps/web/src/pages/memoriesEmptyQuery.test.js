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
 *
 * ABOUT THE FIXTURE
 *
 * __fixtures__/searchResponse.populated.json is SCHEMA-GENERATED, not a
 * captured live response: it was produced by serializing the backend's own
 * SearchResponse / SearchResultItem / MemoryResponse models
 * (apps/api/sourcemind/schemas/memory.py) with `model_dump(mode="json")`, so
 * its shape is the wrapped one the API returns -
 *   { memory: {...}, score, rank, match_type, highlight }
 * - including a populated `highlight`, a null `confidence_score`, a null
 * `highlight`, and total_found / latency_ms. Values are invented. It lets the
 * page be exercised against the real response shape rather than a hand-built
 * flat object (WORKING_STANDARDS rule 7). It does NOT exercise realApi's HTTP
 * request: this suite mocks `api.searchMemories`, and HTTP search coverage
 * against a real stack is outside this patch.
 */

import React from "react";
import { render, screen, act, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import Memories from "./Memories";
import api from "../lib/api";
import REAL_SEARCH_RESPONSE from "./__fixtures__/searchResponse.populated.json";

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

let search;

beforeEach(() => {
    search = jest.spyOn(api, "searchMemories");
});

afterEach(() => {
    jest.restoreAllMocks();
});

describe("blank queries never reach the API", () => {
    test("the initial blank query makes no request and shows the prompt", async () => {
        search.mockResolvedValue(REAL_SEARCH_RESPONSE);

        renderPage();
        await settle();

        expect(search).not.toHaveBeenCalled();
        expect(screen.getByText("Type to search memories")).toBeTruthy();
        expect(screen.queryByTestId("error-state")).toBeNull();
    });

    test("a whitespace-only query makes no request and shows the prompt", async () => {
        search.mockResolvedValue(REAL_SEARCH_RESPONSE);

        renderPage();
        await settle();
        await type("   \t ");
        await settle();

        expect(search).not.toHaveBeenCalled();
        expect(screen.getByText("Type to search memories")).toBeTruthy();
        expect(screen.queryByTestId("error-state")).toBeNull();
    });

    test("changing the mode with a blank query still makes no request", async () => {
        search.mockResolvedValue(REAL_SEARCH_RESPONSE);

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
    test("typing a query searches with that query", async () => {
        search.mockResolvedValue(REAL_SEARCH_RESPONSE);

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
        expect(screen.queryByText("Type to search memories")).toBeNull();
    });

    test("a populated, wrapped response renders every result with its totals", async () => {
        search.mockResolvedValue(REAL_SEARCH_RESPONSE);

        renderPage();
        await settle();
        await type("postgres");
        await settle();

        const [first, second] = REAL_SEARCH_RESPONSE.results;
        const card1 = await screen.findByTestId(`memory-card-${first.memory.id}`);
        const card2 = screen.getByTestId(`memory-card-${second.memory.id}`);

        // Fields read off the wrapped `memory`, plus the metadata beside it.
        expect(card1.textContent).toContain(first.memory.content);
        expect(card1.textContent).toContain("database");
        expect(card1.textContent).toContain("decision");
        expect(card1.textContent).toContain("semantic+keyword");
        expect(card1.textContent).toContain(first.score.toFixed(3));
        expect(card2.textContent).toContain(second.memory.content);
        expect(card2.textContent).toContain("keyword");
        expect(card2.textContent).toContain(second.score.toFixed(3));

        // A null confidence_score and a null highlight (second result) render
        // without error; a populated highlight does not leak markup.
        expect(second.memory.confidence_score).toBeNull();
        expect(second.highlight).toBeNull();
        expect(first.highlight).toContain("<b>");
        expect(document.body.textContent).not.toContain("<b>");
        expect(screen.queryByTestId("error-state")).toBeNull();

        // Totals and latency from the response envelope.
        const meta = screen.getByTestId("results-meta").textContent;
        expect(meta).toContain(`1–${REAL_SEARCH_RESPONSE.results.length} of ${REAL_SEARCH_RESPONSE.total_found}`);
        expect(meta).toContain(`${Math.round(REAL_SEARCH_RESPONSE.latency_ms)} ms`);
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
            slow.resolve(REAL_SEARCH_RESPONSE);
        });
        await settle();

        expect(screen.queryByText(/primary store because it keeps embeddings/)).toBeNull();
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
        search.mockResolvedValue(REAL_SEARCH_RESPONSE);

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
