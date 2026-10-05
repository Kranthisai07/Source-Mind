import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import api from "../lib/api";
import { mockApi } from "../lib/mockApi";
import { resetIdentityScopedCaches } from "../lib/realApi";
import ConflictDetail from "./ConflictDetail";
import Conflicts from "./Conflicts";

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        getConflict: jest.fn(),
        listConflicts: jest.fn(),
        resolveConflict: jest.fn(),
    },
}));

const createdAt = "2026-09-30T12:00:00Z";
const summary = {
    id: "00000000-0000-4000-8000-000000000001",
    status: "open",
    conflict_type: "contradiction",
    severity: "critical",
    similarity_score: 0.92,
    explanation: "The two memories make incompatible claims.",
    memory_a_id: "00000000-0000-4000-8000-00000000000a",
    memory_a_content: "Production uses HNSW.",
    memory_b_id: "00000000-0000-4000-8000-00000000000b",
    memory_b_content: "Production uses IVFFlat.",
    created_at: createdAt,
};

const detail = {
    id: summary.id,
    status: summary.status,
    conflict_type: summary.conflict_type,
    severity: null,
    similarity_score: summary.similarity_score,
    explanation: summary.explanation,
    memory_a: { id: summary.memory_a_id, content: summary.memory_a_content },
    memory_b: { id: summary.memory_b_id, content: summary.memory_b_content },
    reviewed_by: null,
    reviewed_at: null,
    revisit_at: null,
    created_at: createdAt,
};

beforeEach(() => {
    jest.clearAllMocks();
    api.listConflicts.mockResolvedValue({ conflicts: [summary], total: 1, next_cursor: null });
    api.getConflict.mockResolvedValue(detail);
    api.resolveConflict.mockResolvedValue({ status: "ok", resolution_type: "merged" });
});

test("renders the real conflict-list schema without inferred contributors", async () => {
    render(
        <MemoryRouter>
            <Conflicts />
        </MemoryRouter>
    );

    expect(await screen.findByText(summary.memory_a_content)).not.toBeNull();
    expect(screen.getByText(summary.memory_b_content)).not.toBeNull();
    expect(screen.getByTestId("conflict-severity-critical").style.color).toBe("rgb(239, 68, 68)");
    expect(screen.getByTestId(`start-review-${summary.id}`).textContent).toContain("Review");
    expect(screen.getByTestId(`start-review-${summary.id}`).textContent).not.toContain("Start Review");
    expect(api.listConflicts).toHaveBeenCalledWith(undefined, { status: null });
});

test("loads the next conflict page and resets paging when the status changes", async () => {
    const second = {
        ...summary,
        id: "00000000-0000-4000-8000-000000000002",
        memory_a_content: "Second-page memory A.",
        memory_b_content: "Second-page memory B.",
    };
    const resolved = {
        ...summary,
        id: "00000000-0000-4000-8000-000000000003",
        status: "resolved",
        memory_a_content: "Resolved memory A.",
        memory_b_content: "Resolved memory B.",
    };
    api.listConflicts.mockImplementation((_workspaceId, options) => {
        if (options.status === "resolved") {
            return Promise.resolve({ conflicts: [resolved], total: 1, next_cursor: null });
        }
        if (options.cursor === "cursor-page-2") {
            return Promise.resolve({ conflicts: [second], total: 1, next_cursor: null });
        }
        return Promise.resolve({ conflicts: [summary], total: 1, next_cursor: "cursor-page-2" });
    });

    render(
        <MemoryRouter>
            <Conflicts />
        </MemoryRouter>
    );

    expect(await screen.findByText(summary.memory_a_content)).not.toBeNull();
    fireEvent.click(screen.getByTestId("conflicts-load-more"));
    expect(await screen.findByText(second.memory_a_content)).not.toBeNull();
    expect(api.listConflicts).toHaveBeenCalledWith(undefined, {
        status: null,
        cursor: "cursor-page-2",
    });

    fireEvent.click(screen.getByTestId("conflict-tab-resolved"));
    expect(await screen.findByText(resolved.memory_a_content)).not.toBeNull();
    expect(screen.queryByText(summary.memory_a_content)).toBeNull();
    expect(screen.queryByText(second.memory_a_content)).toBeNull();
    expect(api.listConflicts).toHaveBeenLastCalledWith(undefined, { status: "resolved" });
});

test("clears accumulated conflicts when a later page loses access", async () => {
    api.listConflicts
        .mockResolvedValueOnce({ conflicts: [summary], total: 2, next_cursor: "cursor-page-2" })
        .mockRejectedValueOnce({ status: 403 });

    render(
        <MemoryRouter>
            <Conflicts />
        </MemoryRouter>
    );

    expect(await screen.findByText(summary.memory_a_content)).not.toBeNull();
    fireEvent.click(screen.getByTestId("conflicts-load-more"));

    expect(await screen.findByTestId("conflicts-error")).not.toBeNull();
    expect(screen.queryByText(summary.memory_a_content)).toBeNull();
});

test("clears accumulated conflicts when the signed-in identity changes", async () => {
    let finishReload;
    api.listConflicts
        .mockResolvedValueOnce({ conflicts: [summary], total: 1, next_cursor: null })
        .mockImplementationOnce(() => new Promise((resolve) => { finishReload = resolve; }));

    render(
        <MemoryRouter>
            <Conflicts />
        </MemoryRouter>
    );

    expect(await screen.findByText(summary.memory_a_content)).not.toBeNull();
    await act(async () => { resetIdentityScopedCaches(); });

    await waitFor(() => expect(screen.queryByText(summary.memory_a_content)).toBeNull());
    await act(async () => { finishReload({ conflicts: [], total: 0, next_cursor: null }); });
    expect(await screen.findByText("No conflicts in this state.")).toBeTruthy();
});

test("renders nullable conflict detail and submits the supported merged contract", async () => {
    render(
        <MemoryRouter initialEntries={[`/conflicts/${detail.id}`]}>
            <Routes>
                <Route path="/conflicts/:id" element={<ConflictDetail />} />
                <Route path="/conflicts" element={<div>Conflict list</div>} />
            </Routes>
        </MemoryRouter>
    );

    expect(await screen.findByText(detail.memory_a.content)).not.toBeNull();
    expect(screen.getByText(detail.memory_b.content)).not.toBeNull();
    fireEvent.click(screen.getByText("Merge both"));
    fireEvent.change(screen.getByTestId("merged-content"), { target: { value: "HNSW is the production index." } });
    fireEvent.change(screen.getByTestId("resolve-note"), { target: { value: "Consolidated after review." } });
    fireEvent.click(screen.getByTestId("confirm-resolution"));

    await waitFor(() => {
        expect(api.resolveConflict).toHaveBeenCalledWith(detail.id, {
            resolution_type: "merged",
            merged_content: "HNSW is the production index.",
            note: "Consolidated after review.",
        });
    });
});

test("renders a neutral missing state for an unavailable conflict", async () => {
    api.getConflict.mockRejectedValue({
        status: 404,
        body: { error: { code: "SM026", message: "Conflict not found." } },
    });

    render(
        <MemoryRouter initialEntries={[`/conflicts/${detail.id}`]}>
            <Routes>
                <Route path="/conflicts/:id" element={<ConflictDetail />} />
            </Routes>
        </MemoryRouter>
    );

    const state = await screen.findByTestId("error-state");
    expect(state.getAttribute("data-error-kind")).toBe("missing");
    expect(state.textContent).toMatch(/not available/i);
    expect(state.textContent).not.toMatch(/permission|forbidden|denied|exists/i);
});

test("mock conflict resolution preserves action-specific fields", async () => {
    const payload = {
        resolution_type: "split",
        note: "Separate environment-specific claims.",
        merged_content: undefined,
        tag_a: "staging",
        tag_b: "production",
        revisit_at: undefined,
    };

    const response = await mockApi.resolveConflict(detail.id, payload);

    expect(response).toMatchObject({ status: "ok", ...payload });
});
