/**
 * MemoryDetail's Edit / Share / Delete controls.
 *
 * THE DEFECT
 *
 * All three rendered as ordinary enabled buttons with no `onClick`, no
 * `disabled` and no tooltip. They took hover styling, took the click, and did
 * nothing. Delete additionally carried `text-danger`, so it advertised a
 * destructive action that never happened — a user could reasonably believe a
 * memory had been deleted when nothing had been sent anywhere.
 *
 * Handoff's Assign/Complete buttons already solved this honestly: `disabled`,
 * a `title` saying why, and `cursor-not-allowed`. These pin the same treatment
 * here. The underlying workflows stay unimplemented on purpose; what changes is
 * that the screen stops claiming they exist.
 */

import React from "react";
import { render, screen, act, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

const mockGetMemory = jest.fn();
const mockGetVersions = jest.fn();
const mockUpdateMemory = jest.fn();

// react-markdown and remark-gfm ship ESM only and CRA's Jest transform does
// not process them; MemoryDetail reaches both through Markdown.jsx. Stubbed
// here rather than widening transformIgnorePatterns, which would mean editing
// craco.config.js for a test about button state.
jest.mock("react-markdown", () => ({
    __esModule: true,
    default: ({ children }) => children ?? null,
}));
jest.mock("remark-gfm", () => ({ __esModule: true, default: () => {} }));

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        getMemory: (...a) => mockGetMemory(...a),
        getMemoryVersions: (...a) => mockGetVersions(...a),
        updateMemory: (...a) => mockUpdateMemory(...a),
        getMemoryAttribution: jest.fn(async () => ({ attribution: [] })),
        getMemoryEdits: jest.fn(async () => ({ edits: [] })),
    },
}));

import MemoryDetail from "./MemoryDetail";

const settle = async (ms = 250) => {
    await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
};

const renderPage = () =>
    render(
        <MemoryRouter initialEntries={["/memories/m-1"]}>
            <Routes>
                <Route path="/memories/:id" element={<MemoryDetail />} />
            </Routes>
        </MemoryRouter>
    );

beforeEach(() => {
    mockGetMemory.mockReset().mockResolvedValue({
        id: "m-1",
        content: "Adopt PostgreSQL 18 for the primary store.",
        tags: ["decision"],
        importance_score: 0.9,
        created_at: "2026-09-01T00:00:00Z",
        attribution: [],
    });
    mockGetVersions.mockReset().mockResolvedValue({ versions: [], total: 0 });
    mockUpdateMemory.mockReset().mockResolvedValue({ id: "m-1", version: 2 });
});

describe("memory actions", () => {
    test("Edit opens the versioned workflow", async () => {
        renderPage();
        await settle();
        await waitFor(() => expect(screen.getByTestId("mem-edit")).toBeTruthy());

        const btn = screen.getByTestId("mem-edit");
        expect(btn.disabled).toBe(false);
        fireEvent.click(btn);
        expect(await screen.findByTestId("memory-edit-dialog")).toBeTruthy();
    });

    test("Share is disabled and says why", async () => {
        renderPage();
        await settle();
        await waitFor(() => expect(screen.getByTestId("mem-share")).toBeTruthy());

        const btn = screen.getByTestId("mem-share");
        expect(btn.disabled).toBe(true);
        expect(btn.getAttribute("title")).toMatch(/\S/);
    });

    test("Delete is disabled and says why", async () => {
        // The one that matters most: a user who believes this worked believes
        // their memory is gone.
        renderPage();
        await settle();
        await waitFor(() => expect(screen.getByTestId("mem-delete")).toBeTruthy());

        const btn = screen.getByTestId("mem-delete");
        expect(btn.disabled).toBe(true);
        expect(btn.getAttribute("title")).toMatch(/\S/);
    });

    test("unsupported share and delete controls carry no click handler", async () => {
        renderPage();
        await settle();
        await waitFor(() => expect(screen.getByTestId("mem-edit")).toBeTruthy());

        // A disabled button that also has no handler cannot fire even if the
        // disabled attribute is later removed by mistake.
        for (const id of ["mem-share", "mem-delete"]) {
            expect(screen.getByTestId(id).onclick).toBeFalsy();
        }
    });

    test("saving sends PATCH data and reports success", async () => {
        renderPage();
        fireEvent.click(await screen.findByTestId("mem-edit"));

        fireEvent.change(screen.getByTestId("memory-edit-content"), { target: { value: "Use PostgreSQL 18.1." } });
        fireEvent.change(screen.getByTestId("memory-edit-tags"), { target: { value: "decision, database" } });
        fireEvent.click(screen.getByTestId("memory-edit-save"));

        await waitFor(() => expect(mockUpdateMemory).toHaveBeenCalledTimes(1));
        expect(mockUpdateMemory.mock.calls[0][0]).toBe("m-1");
        expect(mockUpdateMemory.mock.calls[0][1]).toMatchObject({
            content: "Use PostgreSQL 18.1.",
            tags: ["decision", "database"],
        });
        expect(mockUpdateMemory.mock.calls[0][1].idempotencyKey).toMatch(/^[0-9a-f-]{36}$/i);
        expect((await screen.findByTestId("memory-edit-success")).textContent).toContain("version 2");
        fireEvent.click(screen.getByTestId("mem-edit"));
        expect(screen.queryByTestId("memory-edit-success")).toBeNull();
    });

    test("failed PATCH retry reuses the idempotency key", async () => {
        mockUpdateMemory.mockRejectedValue(new Error("save failed"));
        renderPage();
        fireEvent.click(await screen.findByTestId("mem-edit"));
        fireEvent.change(screen.getByTestId("memory-edit-content"), { target: { value: "Retry this edit." } });

        fireEvent.click(screen.getByTestId("memory-edit-save"));
        await waitFor(() => expect(mockUpdateMemory).toHaveBeenCalledTimes(1));
        expect(await screen.findByTestId("memory-edit-error")).toBeTruthy();
        fireEvent.click(screen.getByTestId("memory-edit-save"));
        await waitFor(() => expect(mockUpdateMemory).toHaveBeenCalledTimes(2));

        expect(mockUpdateMemory.mock.calls[1][1].idempotencyKey)
            .toBe(mockUpdateMemory.mock.calls[0][1].idempotencyKey);
    });

    test("changed edit after failure receives a new idempotency key", async () => {
        mockUpdateMemory.mockRejectedValue(new Error("save failed"));
        renderPage();
        fireEvent.click(await screen.findByTestId("mem-edit"));
        fireEvent.change(screen.getByTestId("memory-edit-content"), { target: { value: "First edit." } });

        fireEvent.click(screen.getByTestId("memory-edit-save"));
        await waitFor(() => expect(mockUpdateMemory).toHaveBeenCalledTimes(1));
        expect(await screen.findByTestId("memory-edit-error")).toBeTruthy();
        fireEvent.change(screen.getByTestId("memory-edit-content"), { target: { value: "Changed edit." } });
        fireEvent.click(screen.getByTestId("memory-edit-save"));
        await waitFor(() => expect(mockUpdateMemory).toHaveBeenCalledTimes(2));

        expect(mockUpdateMemory.mock.calls[1][1].idempotencyKey)
            .not.toBe(mockUpdateMemory.mock.calls[0][1].idempotencyKey);
    });

    test("shows loading and backend failure states", async () => {
        let rejectUpdate;
        mockUpdateMemory.mockImplementation(() => new Promise((resolve, reject) => { rejectUpdate = reject; }));
        renderPage();
        fireEvent.click(await screen.findByTestId("mem-edit"));
        fireEvent.click(screen.getByTestId("memory-edit-save"));

        expect(screen.getByTestId("memory-edit-save").textContent).toContain("Saving");
        expect(screen.getByTestId("memory-edit-content").disabled).toBe(true);
        expect(screen.getByTestId("memory-edit-tags").disabled).toBe(true);
        await act(async () => { rejectUpdate(new Error("save failed")); });
        expect(await screen.findByTestId("memory-edit-error")).toBeTruthy();
        expect(screen.getByTestId("memory-edit-save").disabled).toBe(false);
    });
});

describe("the supported control is untouched", () => {
    test("Back is still enabled", async () => {
        renderPage();
        await settle();
        await waitFor(() => expect(screen.getByTestId("mem-back")).toBeTruthy());

        expect(screen.getByTestId("mem-back").disabled).toBe(false);
    });
});

describe("version timeline contract", () => {
    test("keeps the version timeline loading until the request settles", async () => {
        let finishVersions;
        mockGetVersions.mockImplementation(() => new Promise((resolve) => { finishVersions = resolve; }));

        renderPage();

        expect(await screen.findByTestId("mem-edit")).toBeTruthy();
        expect(screen.getByTestId("memory-versions-loading")).toBeTruthy();
        expect(screen.queryByText("No version history is available.")).toBeNull();

        await act(async () => { finishVersions({ versions: [], total: 0 }); });
        expect(await screen.findByText("No version history is available.")).toBeTruthy();
    });

    test("renders the backend version fields without inventing an editor", async () => {
        mockGetVersions.mockResolvedValue({
            versions: [
                {
                    id: "v-2",
                    version: 2,
                    is_current: true,
                    content: "Use the versioned backend contract.",
                    created_at: "2026-09-30T12:00:00Z",
                },
                {
                    id: "v-1",
                    version: 1,
                    is_current: false,
                    content: "Earlier contract content.",
                    created_at: "2026-09-29T12:00:00Z",
                },
            ],
            total: 2,
        });

        renderPage();

        const row = await screen.findByTestId("memory-version-2");
        expect(row.textContent).toContain("v2");
        expect(row.textContent).toContain("Current");
        expect(row.textContent).toContain("Use the versioned backend contract.");
        expect(row.querySelector("time")?.getAttribute("datetime")).toBe("2026-09-30T12:00:00Z");
        expect(row.textContent).not.toContain("@undefined");

        const historical = screen.getByTestId("memory-version-1");
        expect(historical.textContent).toContain("Historical");
        expect(historical.textContent).toContain("Earlier contract content.");
    });
});

describe("production attribution contract", () => {
    test("uses the production avatar shape without inventing a login", async () => {
        mockGetMemory.mockResolvedValue({
            id: "m-1",
            content: "Attributed production memory.",
            tags: [],
            created_at: "2026-09-01T00:00:00Z",
            attribution: [{
                author: "Jane Doe",
                name: "Jane Doe",
                avatar_url: "https://example.invalid/avatar.png",
                color: "#34D399",
                score: 1,
                percentage: 100,
                is_primary: true,
                signals: null,
            }],
        });

        renderPage();

        const avatar = await screen.findByTestId("contributor-avatar");
        expect(avatar.getAttribute("title")).toBe("Jane Doe");
        expect(avatar.getAttribute("title")).not.toContain("[object Object]");
        expect(avatar.getAttribute("title")).not.toContain("@");
        expect(screen.getByTestId("contributor-avatar-image").getAttribute("src")).toBe("https://example.invalid/avatar.png");
    });

    test("pilot mode hides attribution percentages and signal details", async () => {
        const previous = process.env.REACT_APP_PILOT_MODE;
        process.env.REACT_APP_PILOT_MODE = "true";
        mockGetMemory.mockResolvedValue({
            id: "m-1",
            content: "Blind contribution rating memory.",
            tags: [],
            created_at: "2026-09-01T00:00:00Z",
            attribution: [{
                author: "Hidden Person",
                name: "Hidden Person",
                score: 0.8,
                percentage: 80,
                is_primary: true,
                signals: { S1: 0.8 },
            }],
        });

        try {
            renderPage();
            expect(await screen.findByTestId("pilot-memory-attribution-hidden")).toBeTruthy();
            expect(screen.queryByText("Attribution Breakdown")).toBeNull();
            expect(screen.queryByText("80%")).toBeNull();
            expect(screen.queryByText("Hidden Person")).toBeNull();
            expect(screen.queryByText("View Conflicts")).toBeNull();
        } finally {
            if (previous === undefined) delete process.env.REACT_APP_PILOT_MODE;
            else process.env.REACT_APP_PILOT_MODE = previous;
        }
    });
});
