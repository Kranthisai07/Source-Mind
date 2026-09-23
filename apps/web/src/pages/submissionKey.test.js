/**
 * Idempotency keys across repeated submissions, driven through the ingest panel.
 *
 * The half that was missing: an earlier version held one key on the PANEL, so
 * it survived any edit. A user whose submit failed, who then changed the text
 * and submitted again, sent the NEW content under the OLD key — which the
 * backend can read as a replay, returning the first job while the edit is
 * silently discarded. That is worse than a duplicate: the user is told it
 * worked, and something else was ingested.
 *
 * A unit test of the key holder alone would not have caught it, because the
 * holder was never wrong — the binding was. So these drive the real component.
 */

import React from "react";
import { render, screen, act, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import Memories from "./Memories";
import { setCurrentUserId } from "../lib/currentUser";

// The module is MOCKED rather than spied on. jest.spyOn against a
// babel-compiled ESM re-export replaces the binding with a spy whose
// implementation is the binding — so calling it recurses until the stack
// blows. That is not hypothetical: it is what the first version of this file
// did, and "Maximum call stack size exceeded" was swallowed by the catch in
// clickSubmit, surfacing only as "createMemory was never called".
const mockSearch = jest.fn();
const mockCreate = jest.fn();
const mockResolve = jest.fn();
const mockJobStatus = jest.fn();

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        searchMemories: (...a) => mockSearch(...a),
        createMemory: (...a) => mockCreate(...a),
        // The panel polls this once a submission is accepted.
        getJobStatus: (...a) => mockJobStatus(...a),
    },
    resolveCurrentWorkspace: (...a) => mockResolve(...a),
}));

const settle = async () => {
    await act(async () => {
        await new Promise((r) => setTimeout(r, 200));
    });
};

function keysSent() {
    return mockCreate.mock.calls.map((c) => c[0].idempotencyKey);
}

async function clickSubmit() {
    try {
        await act(async () => {
            fireEvent.click(screen.getByTestId("ingest-submit"));
        });
    } catch {
        // submit() re-throws when the create rejects. These assertions are
        // about which KEY was sent, not about how the panel surfaces errors.
    }
}

async function openPanelAndType(text) {
    await act(async () => {
        fireEvent.click(screen.getByTestId("ingest-open-btn"));
    });
    await act(async () => {
        fireEvent.change(screen.getByTestId("ingest-content"), {
            target: { value: text },
        });
    });
}

beforeEach(() => {
    mockSearch.mockReset().mockResolvedValue({ results: [], total_found: 0, latency_ms: 1 });
    mockCreate.mockReset();
    mockResolve.mockReset().mockResolvedValue("11111111-1111-4111-8111-111111111111");
    mockJobStatus.mockReset().mockResolvedValue({ status: "done", stages: [] });
    setCurrentUserId("user-a");
});

afterEach(() => {
    setCurrentUserId(null);
});

describe("submission keys", () => {
    test("an UNCHANGED retry after a failure reuses its key", async () => {
        mockCreate.mockRejectedValue({ status: 503, body: null });

        render(<MemoryRouter><Memories /></MemoryRouter>);
        await settle();
        await openPanelAndType("the same text");

        await clickSubmit();
        await settle();
        await clickSubmit();
        await settle();

        const keys = keysSent();
        expect(keys).toHaveLength(2);
        expect(keys[0]).toBeTruthy();
        expect(keys[1]).toBe(keys[0]);
    });

    test("CHANGED content after a failure earns a NEW key", async () => {
        // The defect. Before the fix both attempts carried the same key, so
        // the edited text went out as a replay of the original.
        mockCreate.mockRejectedValue({ status: 503, body: null });

        render(<MemoryRouter><Memories /></MemoryRouter>);
        await settle();
        await openPanelAndType("original text");

        await clickSubmit();
        await settle();

        await act(async () => {
            fireEvent.change(screen.getByTestId("ingest-content"), {
                target: { value: "edited text" },
            });
        });
        await clickSubmit();
        await settle();

        const keys = keysSent();
        expect(keys).toHaveLength(2);
        expect(mockCreate.mock.calls[0][0].content).toBe("original text");
        expect(mockCreate.mock.calls[1][0].content).toBe("edited text");
        expect(keys[1]).not.toBe(keys[0]);
    });

    test("a changed WORKSPACE earns a new key", async () => {
        mockCreate.mockRejectedValue({ status: 503, body: null });

        render(<MemoryRouter><Memories /></MemoryRouter>);
        await settle();
        await openPanelAndType("unchanged text");

        await clickSubmit();
        await settle();

        // Same text, different workspace: a different submission.
        mockResolve.mockResolvedValue("22222222-2222-4222-8222-222222222222");
        await clickSubmit();
        await settle();

        const keys = keysSent();
        expect(keys).toHaveLength(2);
        expect(keys[1]).not.toBe(keys[0]);
        // And the request went to the workspace the key was chosen against.
        expect(mockCreate.mock.calls[1][0].workspace_id)
            .toBe("22222222-2222-4222-8222-222222222222");
    });

    test("a changed USER earns a new key", async () => {
        mockCreate.mockRejectedValue({ status: 503, body: null });

        render(<MemoryRouter><Memories /></MemoryRouter>);
        await settle();
        await openPanelAndType("unchanged text");

        await clickSubmit();
        await settle();

        setCurrentUserId("user-b");
        await clickSubmit();
        await settle();

        const keys = keysSent();
        expect(keys).toHaveLength(2);
        expect(keys[1]).not.toBe(keys[0]);
    });

    test("the workspace used for the key is the one sent with the request", async () => {
        mockCreate.mockResolvedValue({ job_id: "j-1" });

        render(<MemoryRouter><Memories /></MemoryRouter>);
        await settle();
        await openPanelAndType("resolve once");

        await clickSubmit();
        await settle();

        // Resolved by the page, once, and handed to the adapter explicitly —
        // rather than the adapter resolving a second time and possibly
        // differently.
        expect(mockCreate.mock.calls[0][0].workspace_id)
            .toBe("11111111-1111-4111-8111-111111111111");
    });

});
