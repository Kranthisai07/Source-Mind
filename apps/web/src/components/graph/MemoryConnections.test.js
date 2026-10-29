import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const mockRelated = jest.fn();
const mockLineage = jest.fn();
const mockOwners = jest.fn();

jest.mock("../../lib/api", () => ({
    __esModule: true,
    default: {
        getRelatedMemories: (...a) => mockRelated(...a),
        getMemoryLineage: (...a) => mockLineage(...a),
        getMemoryOwners: (...a) => mockOwners(...a),
    },
}));

import MemoryConnections from "./MemoryConnections";

const relatedResponse = (extra = {}) => ({
    memory_id: "m1",
    source: "graph",
    depth_requested: 1,
    depth_served: 1,
    nodes: [
        { id: "m1", version: 2, is_current: true, importance: 0.8, hops: 0, preview: "Focal" },
        { id: "m2", version: 1, is_current: false, importance: 0.3, hops: 1, preview: "Old" },
    ],
    edges: [{ id: "e1", source: "m1", target: "m2", type: "updates", confidence: 0.9 }],
    ...extra,
});

const lineageResponse = {
    memory_id: "m1",
    source: "postgres",
    versions: [
        {
            id: "m1",
            version: 2,
            is_current: true,
            created_at: "2026-10-01T10:00:00Z",
            contributors: [{ user_id: "u1", display_name: "Ada", weight: 0.75, action: "edit" }],
        },
        { id: "m0", version: 1, is_current: false, created_at: null, contributors: [] },
    ],
};

const ownersResponse = {
    memory_id: "m1",
    source: "graph",
    depth_served: 2,
    owners: [
        { user_id: "u1", display_name: "Ada", score: 0.9, direct_weight: 0.9, hops: 0, path: ["m1"] },
        { user_id: "u2", display_name: null, score: 0.2, direct_weight: null, hops: 1, path: ["m1", "m2"] },
    ],
};

const mount = (props = {}) =>
    render(
        <MemoryRouter>
            <MemoryConnections memoryId="m1" {...props} />
        </MemoryRouter>
    );

const open = async (testId) => {
    // Radix tabs activate on mouse down, not click.
    await act(async () => {
        fireEvent.mouseDown(screen.getByTestId(testId));
        fireEvent.focus(screen.getByTestId(testId));
    });
};

beforeEach(() => {
    mockRelated.mockReset().mockResolvedValue(relatedResponse());
    mockLineage.mockReset().mockResolvedValue(lineageResponse);
    mockOwners.mockReset().mockResolvedValue(ownersResponse);
});

describe("MemoryConnections", () => {
    test("opening a memory makes exactly one graph request: the related tab", async () => {
        mount();
        await screen.findByTestId("related-graph-canvas");
        expect(mockRelated).toHaveBeenCalledWith("m1", { depth: 1 });
        expect(mockLineage).not.toHaveBeenCalled();
        expect(mockOwners).not.toHaveBeenCalled();
    });

    test("the hop buttons refetch with the chosen depth", async () => {
        mount();
        await screen.findByTestId("related-graph-canvas");
        await act(async () => {
            fireEvent.click(screen.getByTestId("related-depth-3"));
        });
        await waitFor(() => expect(mockRelated).toHaveBeenLastCalledWith("m1", { depth: 3 }));
        expect(screen.getByTestId("related-depth-3").getAttribute("aria-pressed")).toBe("true");
    });

    test("when only one hop could be served, the page says so instead of pretending", async () => {
        mockRelated.mockResolvedValue(relatedResponse({ depth_requested: 3, depth_served: 1, source: "postgres" }));
        mount();
        const note = await screen.findByTestId("related-graph-note");
        expect(note.textContent).toMatch(/1 hop/);
        expect(screen.getByTestId("related-graph-source").getAttribute("data-source")).toBe("postgres");
    });

    test("the lineage tab lists versions with contributors, and loads only when opened", async () => {
        mount();
        await screen.findByTestId("related-graph-canvas");
        await open("tab-lineage");
        const list = await screen.findByTestId("lineage-list");
        expect(mockLineage).toHaveBeenCalledWith("m1");
        expect(list.textContent).toContain("v2");
        expect(list.textContent).toContain("Ada");
        expect(list.textContent).toContain("75%");
        expect(screen.getByTestId("lineage-version-1").textContent).toContain("No contribution recorded.");
        expect(screen.getByTestId("lineage-source").getAttribute("data-source")).toBe("postgres");
    });

    test("the who-would-know tab ranks people and explains the hop", async () => {
        mount();
        await screen.findByTestId("related-graph-canvas");
        await open("tab-owners");
        const first = await screen.findByTestId("owner-1");
        expect(first.textContent).toContain("Ada");
        expect(first.textContent).toContain("Contributed to this memory");
        const second = screen.getByTestId("owner-2");
        expect(second.textContent).toContain("A contributor"); // no name on record
        expect(second.textContent).toContain("1 hop away");
        expect(second.textContent).toContain("score 0.20");
    });

    test("pilot mode hides attribution: no owners tab, no contributor names in the lineage", async () => {
        mount({ pilotMode: true });
        await screen.findByTestId("related-graph-canvas");
        expect(screen.queryByTestId("tab-owners")).toBeNull();
        await open("tab-lineage");
        const list = await screen.findByTestId("lineage-list");
        expect(list.textContent).toContain("v2");
        expect(list.textContent).not.toContain("Ada");
        expect(list.textContent).not.toContain("75%");
        expect(mockOwners).not.toHaveBeenCalled();
    });

    test("a failed tab shows its own error and a retry that refetches", async () => {
        mockOwners.mockRejectedValueOnce(Object.assign(new Error("down"), { status: 500 }));
        mount();
        await screen.findByTestId("related-graph-canvas");
        await open("tab-owners");
        const box = await screen.findByTestId("owners-error", {}, { timeout: 4000 });
        expect(screen.queryByTestId("owners-empty")).toBeNull();
        await act(async () => {
            fireEvent.click(box.querySelector("button"));
        });
        expect(await screen.findByTestId("owner-1")).not.toBeNull();
    });

    test("a memory nobody contributed to says so, rather than showing an empty list", async () => {
        mockOwners.mockResolvedValue({ ...ownersResponse, owners: [] });
        mount();
        await screen.findByTestId("related-graph-canvas");
        await open("tab-owners");
        expect(await screen.findByTestId("owners-empty")).not.toBeNull();
    });
});
