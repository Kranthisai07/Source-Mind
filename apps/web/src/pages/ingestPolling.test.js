/**
 * Ingestion job polling in the ingest panel.
 *
 * THE DEFECT (Devin 12f6_0003, reproduced against head before any edit)
 *
 * The loop treated exactly one status as terminal:
 *
 *     if (r.status === "done") return;
 *
 * so a job that ends in `failed` is polled forever, and the panel keeps
 * rendering "Processing · stage …" with no way out — a failure displayed as
 * progress, with no reset button, because that button is also gated on
 * `done`.
 *
 * This became reachable on demand rather than theoretical once revocation
 * started terminalising queued documents as `failed`: every revoked upload now
 * lands in exactly the state the loop cannot leave.
 *
 * The loop also had no catch. Once access is lost mid-poll the request
 * rejects, the rejection escapes `poll()` as an unhandled rejection, and the
 * panel is left displaying the job details of a resource the caller may no
 * longer be permitted to see.
 *
 * WHAT THESE ASSERT
 *
 * Polling stops — measured by the request count no longer growing, not by a
 * rendered string, because a component can look idle while a loop runs behind
 * it. Several of these fail against the pre-fix component; that is the point.
 */

import React from "react";
import { render, screen, act, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import Memories from "./Memories";
import { setCurrentUserId } from "../lib/currentUser";

const mockSearch = jest.fn();
const mockCreate = jest.fn();
const mockResolve = jest.fn();
const mockJobStatus = jest.fn();

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        searchMemories: (...a) => mockSearch(...a),
        createMemory: (...a) => mockCreate(...a),
        getJobStatus: (...a) => mockJobStatus(...a),
    },
    resolveCurrentWorkspace: (...a) => mockResolve(...a),
}));

/** The loop waits 180ms between polls; 500ms is several cycles. */
const settle = async (ms = 500) => {
    await act(async () => {
        await new Promise((r) => setTimeout(r, ms));
    });
};

async function openPanelAndSubmit(text = "some content") {
    await act(async () => {
        fireEvent.click(screen.getByTestId("ingest-open-btn"));
    });
    await act(async () => {
        fireEvent.change(screen.getByTestId("ingest-content"), {
            target: { value: text },
        });
    });
    await act(async () => {
        fireEvent.click(screen.getByTestId("ingest-submit"));
    });
}

/** Poll count now, then after several more cycles. Equal => the loop stopped. */
async function pollsStopped() {
    const before = mockJobStatus.mock.calls.length;
    await settle(600);
    return { before, after: mockJobStatus.mock.calls.length };
}

beforeEach(() => {
    mockSearch.mockReset().mockResolvedValue({ results: [], total_found: 0, latency_ms: 1 });
    mockCreate.mockReset().mockResolvedValue({ job_id: "job-1" });
    mockResolve.mockReset().mockResolvedValue("11111111-1111-4111-8111-111111111111");
    mockJobStatus.mockReset();
    setCurrentUserId("user-a");
});

afterEach(() => {
    setCurrentUserId(null);
    jest.restoreAllMocks();
});

describe("a job that ends in failure", () => {
    test("polling STOPS on failed", async () => {
        mockJobStatus.mockResolvedValue({ status: "failed", stage: "extraction", stages: [] });

        render(<MemoryRouter><Memories /></MemoryRouter>);
        await settle();
        await openPanelAndSubmit();
        await settle();

        const { before, after } = await pollsStopped();
        // Pre-fix this grows without bound: only "done" was terminal.
        expect(after).toBe(before);
    });

    test("the panel says it failed rather than showing progress", async () => {
        mockJobStatus.mockResolvedValue({
            status: "failed",
            stage: "extraction",
            stages: [],
            error: "Workspace access revoked before ingestion.",
        });

        render(<MemoryRouter><Memories /></MemoryRouter>);
        await settle();
        await openPanelAndSubmit();
        await settle();

        await waitFor(() => expect(screen.getByTestId("ingest-failed")).toBeTruthy());
        // A failure must not still read as in-flight work.
        expect(screen.queryByText(/Processing · stage/)).toBeNull();
    });

    test("the user can start again after a failure", async () => {
        mockJobStatus.mockResolvedValue({ status: "failed", stage: "extraction", stages: [] });

        render(<MemoryRouter><Memories /></MemoryRouter>);
        await settle();
        await openPanelAndSubmit();
        await settle();

        await waitFor(() => expect(screen.getByTestId("ingest-reset")).toBeTruthy());
    });
});

describe("losing access mid-poll", () => {
    test("a rejected status request stops the loop", async () => {
        mockJobStatus.mockRejectedValue({ status: 403, body: null });

        render(<MemoryRouter><Memories /></MemoryRouter>);
        await settle();
        await openPanelAndSubmit();
        await settle();

        const { before, after } = await pollsStopped();
        expect(after).toBe(before);
    });

    test("protected job details are cleared, not left on screen", async () => {
        // One good poll paints stage detail, then access is lost.
        mockJobStatus
            .mockResolvedValueOnce({ status: "processing", stage: "embedding", stages: [] })
            .mockRejectedValue({ status: 403, body: null });

        render(<MemoryRouter><Memories /></MemoryRouter>);
        await settle();
        await openPanelAndSubmit();
        await settle();

        await waitFor(() => expect(screen.getByTestId("ingest-failed")).toBeTruthy());
        // The stage of a resource we may no longer be allowed to see.
        expect(screen.queryByText(/embedding/)).toBeNull();
    });
});

describe("the loop does not outlive its panel", () => {
    test("unmount stops polling", async () => {
        mockJobStatus.mockResolvedValue({ status: "processing", stage: "chunking", stages: [] });

        const { unmount } = render(<MemoryRouter><Memories /></MemoryRouter>);
        await settle();
        await openPanelAndSubmit();
        await settle();

        unmount();
        const before = mockJobStatus.mock.calls.length;
        await settle(600);
        expect(mockJobStatus.mock.calls.length).toBe(before);
    });

    test("closing the panel stops polling", async () => {
        mockJobStatus.mockResolvedValue({ status: "processing", stage: "chunking", stages: [] });

        render(<MemoryRouter><Memories /></MemoryRouter>);
        await settle();
        await openPanelAndSubmit();
        await settle();

        // Escape is how the sheet actually closes; clicking the open button
        // again just re-opens it. Closing runs reset(), which clears jobId and
        // so tears the loop down.
        await act(async () => {
            fireEvent.keyDown(document, { key: "Escape", code: "Escape" });
        });
        await settle();
        await waitFor(() => expect(screen.queryByTestId("ingest-panel")).toBeNull());

        const { before, after } = await pollsStopped();
        expect(after).toBe(before);
    });

    test("an identity change stops polling", async () => {
        mockJobStatus.mockResolvedValue({ status: "processing", stage: "chunking", stages: [] });

        render(<MemoryRouter><Memories /></MemoryRouter>);
        await settle();
        await openPanelAndSubmit();
        await settle();

        // The job belongs to the previous user; it must not keep being polled
        // under the new one.
        await act(async () => { setCurrentUserId("user-b"); });
        await settle();

        const { before, after } = await pollsStopped();
        expect(after).toBe(before);
    });
});

describe("recovery never resubmits", () => {
    test("a rejected status request does not re-create the memory", async () => {
        mockJobStatus.mockRejectedValue({ status: 503, body: null });

        render(<MemoryRouter><Memories /></MemoryRouter>);
        await settle();
        await openPanelAndSubmit();
        await settle(800);

        // The whole hazard: retrying a poll must never look like retrying the
        // submission. One ingestion was requested, so exactly one was sent.
        expect(mockCreate).toHaveBeenCalledTimes(1);
    });
});

describe("the successful path still works", () => {
    test("done stops polling and offers another ingest", async () => {
        mockJobStatus.mockResolvedValue({
            status: "done", stage: "complete", stages: [], elapsed_ms: 1234,
        });

        render(<MemoryRouter><Memories /></MemoryRouter>);
        await settle();
        await openPanelAndSubmit();
        await settle();

        await waitFor(() =>
            expect(screen.getByText(/Pipeline complete/)).toBeTruthy()
        );
        expect(screen.queryByTestId("ingest-failed")).toBeNull();

        const { before, after } = await pollsStopped();
        expect(after).toBe(before);
    });
});
