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
import { render, screen, act, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

const mockGetMemory = jest.fn();
const mockGetVersions = jest.fn();

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
});

describe("unsupported actions are disabled, not merely inert", () => {
    test("Edit is disabled and says why", async () => {
        renderPage();
        await settle();
        await waitFor(() => expect(screen.getByTestId("mem-edit")).toBeTruthy());

        const btn = screen.getByTestId("mem-edit");
        expect(btn.disabled).toBe(true);
        expect(btn.getAttribute("title")).toMatch(/\S/);
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

    test("none of the three carries a click handler", async () => {
        renderPage();
        await settle();
        await waitFor(() => expect(screen.getByTestId("mem-edit")).toBeTruthy());

        // A disabled button that also has no handler cannot fire even if the
        // disabled attribute is later removed by mistake.
        for (const id of ["mem-edit", "mem-share", "mem-delete"]) {
            expect(screen.getByTestId(id).onclick).toBeFalsy();
        }
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
