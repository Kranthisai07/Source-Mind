import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import api from "../lib/api";
import { mockApi } from "../lib/mockApi";
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
    expect(api.listConflicts).toHaveBeenCalledWith(undefined, { status: null });
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
