/**
 * Editing a memory that may have been replaced (D-027, consuming the
 * superseded-versions contract).
 *
 * Rules these pin: a draft is never lost to a close, an error or a reopen;
 * every failure class is worded for what is actually known; an unknown save
 * outcome is checked with facts and never concluded either way; and
 * updateMemory runs once per explicit Save click and never by itself.
 */

import React from "react";
import { render, screen, act, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

const mockGetMemory = jest.fn();
const mockGetVersions = jest.fn();
const mockUpdateMemory = jest.fn();

jest.mock("react-markdown", () => ({ __esModule: true, default: ({ children }) => children ?? null }));
jest.mock("remark-gfm", () => ({ __esModule: true, default: () => {} }));

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        getMemory: (...a) => mockGetMemory(...a),
        getMemoryVersions: (...a) => mockGetVersions(...a),
        updateMemory: (...a) => mockUpdateMemory(...a),
    },
}));

import MemoryDetail from "./MemoryDetail";
import { setCurrentUserId } from "../lib/currentUser";
import { loadDraft } from "../lib/editDrafts";

const OLD = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const NEW = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

const memory = (overrides = {}) => ({
    id: OLD,
    workspace_id: "w-1",
    content: "Original text.",
    tags: ["decision"],
    version: 1,
    created_at: "2026-09-01T00:00:00Z",
    attribution: [],
    is_current: true,
    latest_memory_id: null,
    supersession_reason: null,
    ...overrides,
});

const settle = async (ms = 100) => {
    await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
};

function renderAt(id = OLD) {
    return render(
        <MemoryRouter initialEntries={[`/memories/${id}`]}>
            <Routes>
                <Route path="/memories/:id" element={<MemoryDetail />} />
            </Routes>
        </MemoryRouter>
    );
}

const apiError = (status, code, details, message = "server message") => ({
    status,
    body: { error: { code, message, details: details ?? null } },
});

async function openAndType(text = "My edited text.") {
    fireEvent.click(await screen.findByTestId("mem-edit"));
    fireEvent.change(screen.getByTestId("memory-edit-content"), { target: { value: text } });
}

const save = () => act(async () => { fireEvent.click(screen.getByTestId("memory-edit-save")); });

beforeEach(() => {
    window.sessionStorage.clear();
    setCurrentUserId("user-a");
    mockGetMemory.mockReset().mockImplementation(async (id) => memory({ id }));
    mockGetVersions.mockReset().mockResolvedValue({ versions: [], total: 0 });
    mockUpdateMemory.mockReset().mockResolvedValue({ id: OLD, version: 2 });
});

afterEach(() => {
    setCurrentUserId(null);
    jest.restoreAllMocks();
});

describe("draft preservation", () => {
    test("closing and reopening the dialog keeps the draft", async () => {
        renderAt();
        await openAndType("Draft that must survive.");
        fireEvent.click(screen.getByText("Cancel"));
        await waitFor(() => expect(screen.queryByTestId("memory-edit-dialog")).toBeNull());
        expect(screen.getByTestId("memory-draft-notice")).toBeTruthy();

        fireEvent.click(screen.getByTestId("mem-edit"));
        expect(screen.getByTestId("memory-edit-content").value).toBe("Draft that must survive.");
    });

    test("a failed save, then reopening, keeps the draft", async () => {
        mockUpdateMemory.mockRejectedValue(apiError(422, "SM010", null, "Content is too long."));
        renderAt();
        await openAndType("Draft after an error.");
        await save();
        expect((await screen.findByTestId("memory-edit-error")).textContent).toBe("Content is too long.");

        fireEvent.click(screen.getByText("Cancel"));
        await waitFor(() => expect(screen.queryByTestId("memory-edit-dialog")).toBeNull());
        fireEvent.click(screen.getByTestId("mem-edit"));
        expect(screen.getByTestId("memory-edit-content").value).toBe("Draft after an error.");
    });

    test("the draft is mirrored to sessionStorage per user and memory and restored on remount", async () => {
        const first = renderAt();
        await openAndType("Persisted draft.");
        expect(loadDraft("user-a", OLD)).toMatchObject({ content: "Persisted draft." });
        expect(loadDraft("user-b", OLD)).toBeNull();
        expect(loadDraft("user-a", NEW)).toBeNull();
        first.unmount();

        renderAt();
        fireEvent.click(await screen.findByTestId("mem-edit"));
        expect(screen.getByTestId("memory-edit-content").value).toBe("Persisted draft.");
    });

    test("a storage failure never breaks editing", async () => {
        jest.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("quota"); });
        jest.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied"); });
        renderAt();
        await openAndType("Typed with broken storage.");

        expect(screen.getByTestId("memory-edit-content").value).toBe("Typed with broken storage.");
        await save();
        await waitFor(() => expect(mockUpdateMemory).toHaveBeenCalledTimes(1));
    });

    test("a successful save clears the draft", async () => {
        renderAt();
        await openAndType("Will be saved.");
        await save();
        await screen.findByTestId("memory-edit-success");

        expect(loadDraft("user-a", OLD)).toBeNull();
        expect(screen.queryByTestId("memory-draft-notice")).toBeNull();
        fireEvent.click(screen.getByTestId("mem-edit"));
        expect(screen.getByTestId("memory-edit-content").value).toBe("Original text.");
    });

    test("Discard draft clears it explicitly", async () => {
        renderAt();
        await openAndType("Throw away.");
        fireEvent.click(screen.getByTestId("memory-edit-discard"));

        expect(loadDraft("user-a", OLD)).toBeNull();
        expect(screen.getByTestId("memory-edit-content").value).toBe("Original text.");
    });
});

describe("409 SM035: the version was replaced", () => {
    test("superseded with a latest id links to it by navigation only", async () => {
        mockUpdateMemory.mockRejectedValue(apiError(409, "SM035", {
            memory_id: OLD, latest_memory_id: NEW, reason: "superseded",
        }));
        renderAt();
        await openAndType("My draft for the old version.");
        await save();

        const banner = await screen.findByTestId("memory-edit-conflict");
        expect(banner.textContent).toContain("This version was replaced by a newer one");
        expect(screen.getByTestId("memory-edit-view-latest")).toBeTruthy();
        // The draft is kept, and nothing was resubmitted.
        expect(screen.getByTestId("memory-edit-content").value).toBe("My draft for the old version.");
        expect(mockUpdateMemory).toHaveBeenCalledTimes(1);
    });

    test.each([
        ["ambiguous_successor", /More than one newer version/],
        ["no_current_version", /no current version/],
        ["superseded_without_successor", /no newer version to open/],
    ])("%s explains there is no single newer version and renders no link", async (reason, wording) => {
        mockUpdateMemory.mockRejectedValue(apiError(409, "SM035", {
            memory_id: OLD, latest_memory_id: null, reason,
        }));
        renderAt();
        await openAndType();
        await save();

        const banner = await screen.findByTestId("memory-edit-conflict");
        expect(banner.textContent).toMatch(wording);
        expect(screen.queryByTestId("memory-edit-view-latest")).toBeNull();
    });

    test("a latest id is ignored unless the reason is superseded", async () => {
        mockUpdateMemory.mockRejectedValue(apiError(409, "SM035", {
            memory_id: OLD, latest_memory_id: NEW, reason: "ambiguous_successor",
        }));
        renderAt();
        await openAndType();
        await save();

        await screen.findByTestId("memory-edit-conflict");
        expect(screen.queryByTestId("memory-edit-view-latest")).toBeNull();
    });
});

describe("other error classes", () => {
    test("404 is generic and implies nothing about existence", async () => {
        mockUpdateMemory.mockRejectedValue(apiError(404, "SM020", null, "Memory abc not found or not current version"));
        renderAt();
        await openAndType();
        await save();

        const notFound = await screen.findByTestId("memory-edit-notfound");
        expect(notFound.textContent).toMatch(/not found, or you don't have access/);
        expect(notFound.textContent).not.toMatch(/current version|replaced|newer/);
        expect(screen.queryByTestId("memory-edit-view-latest")).toBeNull();
    });

    test.each([
        ["a network error", { status: 0, network: true, body: null }],
        ["a 503", { status: 503, body: null }],
    ])("%s reports an unknown outcome and keeps the draft", async (_label, failure) => {
        mockUpdateMemory.mockRejectedValue(failure);
        renderAt();
        await openAndType("Unconfirmed draft.");
        await save();

        const banner = await screen.findByTestId("memory-edit-unknown");
        expect(banner.textContent).toContain("Save outcome unknown. Your draft is kept. We can't tell whether it was applied until you check.");
        expect(screen.getByTestId("memory-edit-check-history")).toBeTruthy();
        expect(screen.getByTestId("memory-edit-content").value).toBe("Unconfirmed draft.");
        expect(screen.getByTestId("memory-edit-dialog").textContent).not.toMatch(/\bsaved\b/i);
        expect(screen.queryByTestId("memory-edit-success")).toBeNull();
    });

    test("another 4xx shows the server message", async () => {
        mockUpdateMemory.mockRejectedValue(apiError(403, "SM005", null, "You may not edit this."));
        renderAt();
        await openAndType();
        await save();

        expect((await screen.findByTestId("memory-edit-error")).textContent).toBe("You may not edit this.");
    });
});

describe("Check history", () => {
    test("shows server facts and never concludes success, even when the text equals the draft", async () => {
        mockUpdateMemory.mockRejectedValue({ status: 0, network: true, body: null });
        renderAt();
        await openAndType("Identical text.");
        await save();
        await screen.findByTestId("memory-edit-unknown");

        // The server now holds a version whose content equals the draft.
        mockGetMemory.mockResolvedValue(memory({ version: 2, content: "Identical text.", updated_at: "2026-09-02T10:00:00Z" }));
        mockGetVersions.mockResolvedValue({
            versions: [
                { id: "v2", version: 2, is_current: true, content: "Identical text.", created_at: "2026-09-02T10:00:00Z" },
                { id: "v1", version: 1, is_current: false, content: "Original text.", created_at: "2026-09-01T00:00:00Z" },
            ],
            total: 2,
        });
        await act(async () => { fireEvent.click(screen.getByTestId("memory-edit-check-history")); });

        const facts = await screen.findByTestId("memory-edit-history");
        expect(screen.getByTestId("memory-history-current").textContent).toMatch(/Current version: v2/);
        expect(screen.getByTestId("memory-history-version-2").textContent).toContain("Identical text.");
        expect(screen.getByTestId("memory-history-version-1").textContent).toContain("Original text.");
        expect(facts.textContent).toMatch(/Nothing has been resubmitted/);
        // No verdict in either direction.
        expect(facts.textContent).not.toMatch(/was saved|were saved|succeeded|applied|did not save|failed/i);
        expect(screen.queryByTestId("memory-edit-success")).toBeNull();
        // Reads only: the check did not resubmit anything.
        expect(mockUpdateMemory).toHaveBeenCalledTimes(1);
        expect(screen.getByTestId("memory-edit-content").value).toBe("Identical text.");
    });

    test("reports a failed read as a failed read", async () => {
        mockUpdateMemory.mockRejectedValue({ status: 500, body: null });
        renderAt();
        await openAndType();
        await save();
        await screen.findByTestId("memory-edit-unknown");

        mockGetMemory.mockRejectedValue({ status: 503, body: null });
        mockGetVersions.mockRejectedValue({ status: 503, body: null });
        await act(async () => { fireEvent.click(screen.getByTestId("memory-edit-check-history")); });

        const facts = await screen.findByTestId("memory-edit-history");
        expect(facts.textContent).toMatch(/could not be loaded/);
    });
});

describe("updateMemory is only ever an explicit Save click", () => {
    test("nothing is sent until Save, and one click sends exactly one request", async () => {
        renderAt();
        await openAndType("Not yet saved.");
        await settle(300);
        expect(mockUpdateMemory).not.toHaveBeenCalled();

        const button = screen.getByTestId("memory-edit-save");
        await act(async () => {
            fireEvent.click(button);
            fireEvent.click(button);
        });
        await settle();
        expect(mockUpdateMemory).toHaveBeenCalledTimes(1);
    });

    test("errors, history checks, reopening and navigation never resubmit", async () => {
        mockUpdateMemory.mockRejectedValue({ status: 0, network: true, body: null });
        renderAt();
        await openAndType();
        await save();
        await screen.findByTestId("memory-edit-unknown");
        await act(async () => { fireEvent.click(screen.getByTestId("memory-edit-check-history")); });
        await settle(300);
        fireEvent.click(screen.getByText("Cancel"));
        await waitFor(() => expect(screen.queryByTestId("memory-edit-dialog")).toBeNull());
        fireEvent.click(screen.getByTestId("mem-edit"));
        await settle(300);

        expect(mockUpdateMemory).toHaveBeenCalledTimes(1);
    });
});

describe("a superseded version on load", () => {
    test("shows a banner linking to the latest, and Edit is disabled", async () => {
        mockGetMemory.mockImplementation(async (id) =>
            id === OLD
                ? memory({ is_current: false, latest_memory_id: NEW, supersession_reason: "superseded" })
                : memory({ id, content: "Newer text.", version: 2 })
        );
        renderAt();

        const banner = await screen.findByTestId("memory-superseded-banner");
        expect(banner.textContent).toContain("This version was replaced by a newer one");
        expect(screen.getByTestId("memory-superseded-view-latest")).toBeTruthy();
        expect(screen.getByTestId("mem-edit").disabled).toBe(true);
    });

    test("without a single successor it explains and renders no link", async () => {
        mockGetMemory.mockResolvedValue(
            memory({ is_current: false, latest_memory_id: null, supersession_reason: "ambiguous_successor" })
        );
        renderAt();

        const banner = await screen.findByTestId("memory-superseded-banner");
        expect(banner.textContent).toMatch(/More than one newer version/);
        expect(screen.queryByTestId("memory-superseded-view-latest")).toBeNull();
        expect(screen.getByTestId("mem-edit").disabled).toBe(true);
    });

    test("a response without the new fields behaves as current", async () => {
        mockGetMemory.mockResolvedValue({ id: OLD, content: "Old backend.", tags: [], created_at: "2026-09-01T00:00:00Z", attribution: [] });
        renderAt();

        await screen.findByTestId("mem-edit");
        expect(screen.queryByTestId("memory-superseded-banner")).toBeNull();
        expect(screen.getByTestId("mem-edit").disabled).toBe(false);
    });
});

describe("carrying a draft to the latest version", () => {
    test("navigation preserves the draft and the latest page only OFFERS to open it", async () => {
        mockUpdateMemory.mockRejectedValue(apiError(409, "SM035", {
            memory_id: OLD, latest_memory_id: NEW, reason: "superseded",
        }));
        renderAt();
        await openAndType("Carry me over.");
        await save();
        const viewLatest = await screen.findByTestId("memory-edit-view-latest");
        await act(async () => { fireEvent.click(viewLatest); });

        // Now on the latest version: banner offers, nothing opened or sent.
        const offer = await screen.findByTestId("memory-carried-draft");
        expect(offer.textContent).toMatch(/unsaved draft/);
        expect(screen.queryByTestId("memory-edit-dialog")).toBeNull();
        expect(mockUpdateMemory).toHaveBeenCalledTimes(1);
        await settle(300);
        expect(mockUpdateMemory).toHaveBeenCalledTimes(1);

        await act(async () => { fireEvent.click(screen.getByTestId("memory-carried-open")); });
        expect(screen.getByTestId("memory-edit-content").value).toBe("Carry me over.");
        expect(loadDraft("user-a", NEW)).toMatchObject({ content: "Carry me over." });
        expect(screen.queryByTestId("memory-carried-draft")).toBeNull();
        // Opening the editor is still not saving.
        expect(mockUpdateMemory).toHaveBeenCalledTimes(1);
    });

    test("the offer can be discarded", async () => {
        mockUpdateMemory.mockRejectedValue(apiError(409, "SM035", {
            memory_id: OLD, latest_memory_id: NEW, reason: "superseded",
        }));
        renderAt();
        await openAndType("Discard me.");
        await save();
        const viewLatest = await screen.findByTestId("memory-edit-view-latest");
        await act(async () => { fireEvent.click(viewLatest); });
        await screen.findByTestId("memory-carried-draft");

        fireEvent.click(screen.getByTestId("memory-carried-discard"));
        expect(screen.queryByTestId("memory-carried-draft")).toBeNull();
        expect(loadDraft("user-a", NEW)).toBeNull();
        expect(loadDraft("user-a", OLD)).toBeNull();
    });

    test("View latest on a superseded page carries the draft too", async () => {
        window.sessionStorage.clear();
        mockGetMemory.mockImplementation(async (id) =>
            id === OLD
                ? memory({ is_current: false, latest_memory_id: NEW, supersession_reason: "superseded" })
                : memory({ id, version: 2 })
        );
        // A draft left from an earlier session on the old version.
        require("../lib/editDrafts").saveDraft("user-a", OLD, { content: "Left earlier.", tags: "" });
        renderAt();

        const viewLatest = await screen.findByTestId("memory-superseded-view-latest");
        await act(async () => { fireEvent.click(viewLatest); });
        expect((await screen.findByTestId("memory-carried-draft"))).toBeTruthy();
    });
});
