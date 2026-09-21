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

/*
 * A recoverable worker exception now persists `processing` with
 * `pipeline_data.current_stage = "retrying"`, and the job route reports
 * `status = current_stage` (api/v1/memories.py:170). So the wire carries
 * `retrying`, which is neither terminal state and must keep the loop running —
 * otherwise the fix for "failed polls for ever" would turn every transient
 * error into a permanent-looking failure.
 *
 * The panel polls the UI vocabulary, and `lib/realApi.js:523` is the only
 * thing that translates: `completed` becomes `done`, everything else passes
 * through untouched. The mock below mirrors exactly that one line, and the
 * first test here guards the mirror against drift by exercising the real
 * adapter over a stubbed fetch. Without that guard, a change to the adapter
 * would leave these cases passing against a contract that no longer exists.
 */
const normalizeLikeAdapter = (backendResponse) => ({
    ...backendResponse,
    status: backendResponse.status === "completed" ? "done" : backendResponse.status,
});

describe("the wire contract these cases are written against", () => {
    test("the adapter maps completed to done and passes retrying through", async () => {
        const { realApi, resetIdentityScopedCaches } = require("../lib/realApi");
        const { setTokenGetter } = require("../lib/authToken");
        setTokenGetter(null);

        const reply = (body) => {
            global.fetch = jest.fn(async () => ({
                ok: true,
                status: 200,
                statusText: "200",
                headers: { get: () => null },
                text: async () => JSON.stringify({ data: body }),
            }));
        };

        try {
            reply({ job_id: "j", status: "completed", stage: "completed" });
            expect((await realApi.getJobStatus("j")).status).toBe("done");

            // Not terminal, and deliberately NOT rewritten by the adapter.
            reply({ job_id: "j", status: "retrying", stage: "retrying", error: null });
            expect((await realApi.getJobStatus("j")).status).toBe("retrying");

            reply({ job_id: "j", status: "failed", stage: "failed" });
            expect((await realApi.getJobStatus("j")).status).toBe("failed");
        } finally {
            delete global.fetch;
            resetIdentityScopedCaches();
        }
    });
});

describe("a transient failure that the worker retries", () => {
    test("retrying keeps polling, then completion stops it and is displayed", async () => {
        // The real sequence: two retry windows, then the retry succeeds.
        const wire = [
            { job_id: "j", status: "retrying", stage: "retrying", error: null, stages: [] },
            { job_id: "j", status: "retrying", stage: "retrying", error: null, stages: [] },
            { job_id: "j", status: "completed", stage: "completed", stages: [], elapsed_ms: 2400 },
        ];
        let i = 0;
        mockJobStatus.mockImplementation(async () =>
            normalizeLikeAdapter(wire[Math.min(i++, wire.length - 1)])
        );

        render(<MemoryRouter><Memories /></MemoryRouter>);
        await settle();
        await openPanelAndSubmit();
        await settle();

        // It must have polled PAST the retrying responses rather than stopping
        // on the first one.
        await waitFor(() => expect(mockJobStatus.mock.calls.length).toBeGreaterThanOrEqual(3));

        // Completion is reached and rendered — a retried job is not a failure.
        await waitFor(() =>
            expect(screen.getByText(/Pipeline complete/)).toBeTruthy()
        );
        expect(screen.queryByTestId("ingest-failed")).toBeNull();

        // And the loop stops there.
        const { before, after } = await pollsStopped();
        expect(after).toBe(before);

        // The whole point: none of that retrying re-sent the submission.
        expect(mockCreate).toHaveBeenCalledTimes(1);
    });

    test("retrying is never rendered as a terminal failure", async () => {
        mockJobStatus.mockImplementation(async () =>
            normalizeLikeAdapter({
                job_id: "j", status: "retrying", stage: "retrying", error: null, stages: [],
            })
        );

        render(<MemoryRouter><Memories /></MemoryRouter>);
        await settle();
        await openPanelAndSubmit();
        await settle();

        expect(screen.queryByTestId("ingest-failed")).toBeNull();
        expect(screen.queryByTestId("ingest-reset")).toBeNull();

        // Still going, because the backend has not reached a terminal state.
        const before = mockJobStatus.mock.calls.length;
        await settle(600);
        expect(mockJobStatus.mock.calls.length).toBeGreaterThan(before);
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
