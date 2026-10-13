/**
 * Memory creation outcomes and retry controls in the ingest panel (D-027).
 *
 * The real API reports no stage list. A job that finishes can have produced
 * several memories, one, or none, and the panel has to say which, using the
 * polled `status`, `memory_ids`, `memory_count`, `error` and `message`. Resubmitting
 * the SAME text returns the same deduplicated record, so retry controls exist
 * only where a retry can actually change the outcome.
 */

import React from "react";
import { render, screen, act, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

import Memories from "./Memories";
import { setCurrentUserId } from "../lib/currentUser";
import { INGEST_TIMING } from "../lib/ingestOutcome";
import { resetIdentityScopedCaches } from "../lib/realApi";

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

const WS = "11111111-1111-4111-8111-111111111111";
const ID_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const ID_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const ID_C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";

const settle = async (ms = 400) => {
    await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
};

const renderPage = () =>
    render(
        <MemoryRouter initialEntries={["/memories"]}>
            <Routes>
                <Route path="/memories" element={<Memories />} />
                <Route path="/memories/:id" element={<div data-testid="memory-page" />} />
            </Routes>
        </MemoryRouter>
    );

async function openPanel() {
    await settle(200);
    await act(async () => { fireEvent.click(screen.getByTestId("ingest-open-btn")); });
}

async function typeAndSubmit(text) {
    await act(async () => {
        fireEvent.change(screen.getByTestId("ingest-content"), { target: { value: text } });
    });
    await act(async () => { fireEvent.click(screen.getByTestId("ingest-submit")); });
}

async function submitText(text = "some content") {
    renderPage();
    await openPanel();
    await typeAndSubmit(text);
}

const timing = { ...INGEST_TIMING };

beforeEach(() => {
    mockSearch.mockReset().mockResolvedValue({ results: [], total_found: 0, latency_ms: 1 });
    mockCreate.mockReset().mockResolvedValue({ job_id: "job-1", status: "queued", already_exists: false });
    mockResolve.mockReset().mockResolvedValue(WS);
    mockJobStatus.mockReset();
    setCurrentUserId("user-a");
});

afterEach(() => {
    Object.assign(INGEST_TIMING, timing);
    setCurrentUserId(null);
    delete process.env.REACT_APP_PILOT_MODE;
    jest.restoreAllMocks();
});

describe("what the ingest sheet says up front", () => {
    test("normal mode explains that text becomes searchable facts, possibly several, one or none", async () => {
        renderPage();
        await openPanel();

        const copy = screen.getByTestId("ingest-explainer").textContent;
        expect(copy).toMatch(/processed into searchable facts/);
        expect(copy).toMatch(/several memories, one, or none/);
        expect(copy).toMatch(/not stored word for word/);
        expect(screen.getByTestId("ingest-panel").textContent).not.toMatch(/extract, chunk, embed/);
    });

    test("normal mode still sends no ingestion_mode", async () => {
        mockJobStatus.mockResolvedValue({ status: "extracting" });
        await submitText("plain text");
        await settle();

        expect(mockCreate).toHaveBeenCalledTimes(1);
        expect(mockCreate.mock.calls[0][0]).not.toHaveProperty("ingestion_mode");
    });

    test("pilot copy and behaviour are unchanged", async () => {
        process.env.REACT_APP_PILOT_MODE = "true";
        mockJobStatus.mockResolvedValue({ status: "extracting" });
        renderPage();
        await openPanel();

        expect(screen.queryByTestId("ingest-explainer")).toBeNull();
        expect(screen.getByText("Create Pilot Memory")).toBeTruthy();
        expect(screen.getByText("Verbatim mode stores this text as one memory without fact extraction.")).toBeTruthy();
        expect(screen.getByTestId("pilot-verbatim-notice").textContent).toContain("submission mode: verbatim");
        expect(screen.getByTestId("ingest-submit").textContent).toBe("Create Verbatim Memory");

        await typeAndSubmit("pilot text");
        await settle();
        expect(mockCreate.mock.calls[0][0].ingestion_mode).toBe("verbatim");
    });
});

describe("progress is reported honestly", () => {
    test.each([
        ["queued", /Queued/],
        ["extracting", /Reading the text/],
        ["chunking", /Splitting the text/],
        ["extracting_facts", /Extracting facts/],
        ["embedding", /Preparing facts for search/],
        ["attributing", /who contributed/],
        ["indexing", /Saving memories/],
        ["processing", /^Processing$/],
    ])("status %s is labelled from the status, without a stage list", async (status, label) => {
        mockJobStatus.mockResolvedValue({ status });
        await submitText();
        await settle();

        expect(screen.getByTestId("ingest-status-label").textContent).toMatch(label);
        expect(screen.queryByTestId("pipeline-tracker")).toBeNull();
        expect(screen.queryByText(/Pipeline complete/)).toBeNull();
    });

    test("retrying says a temporary error occurred and is not a failure", async () => {
        mockJobStatus.mockResolvedValue({ status: "retrying", error: null });
        await submitText();
        await settle();

        expect(screen.getByTestId("ingest-status-label").textContent)
            .toBe("A temporary error occurred; retrying automatically");
        expect(screen.queryByTestId("ingest-failed")).toBeNull();
        expect(screen.queryByTestId("ingest-edit-again")).toBeNull();
    });
});

describe("terminal outcomes are explicit and distinct", () => {
    test("success lists every created memory with a link and the measured time", async () => {
        mockJobStatus.mockResolvedValue({
            status: "done", memory_ids: [ID_A, ID_B, ID_C], memory_count: 3,
        });
        await submitText();
        await settle();

        expect(screen.getByTestId("ingest-success").textContent).toContain("Created 3 memories");
        const links = screen.getByTestId("ingest-memory-links").querySelectorAll("a");
        expect(Array.from(links).map((a) => a.getAttribute("href")))
            .toEqual([`/memories/${ID_A}`, `/memories/${ID_B}`, `/memories/${ID_C}`]);
        expect(screen.getByTestId("ingest-elapsed").textContent).toMatch(/Measured in your browser: \d+\.\d s/);
        expect(screen.queryByText(/Pipeline complete/)).toBeNull();
    });

    test("one memory is worded in the singular", async () => {
        mockJobStatus.mockResolvedValue({ status: "done", memory_ids: [ID_A], memory_count: 1 });
        await submitText();
        await settle();

        expect(screen.getByTestId("ingest-success").textContent).toContain("Created 1 memory");
    });

    test("a link navigates to the memory and closes the sheet", async () => {
        mockJobStatus.mockResolvedValue({ status: "done", memory_ids: [ID_A], memory_count: 1 });
        await submitText();
        await settle();

        await act(async () => { fireEvent.click(screen.getByTestId("ingest-memory-link-0")); });
        expect(await screen.findByTestId("memory-page")).toBeTruthy();
        expect(screen.queryByTestId("ingest-panel")).toBeNull();
    });

    test("zero memories is an explicit amber state, never a success", async () => {
        mockJobStatus.mockResolvedValue({
            status: "done", memory_ids: [], memory_count: 0, error: null, message: "Ingestion complete.",
        });
        await submitText();
        await settle();

        const empty = screen.getByTestId("ingest-empty");
        expect(empty.textContent).toContain("Processing finished but no searchable memory was created");
        expect(screen.queryByTestId("ingest-success")).toBeNull();
        expect(screen.queryByText(/Created \d+/)).toBeNull();
        expect(empty.querySelector("svg")).toBeNull();
    });

    test("a completed job with no memory fields is not success", async () => {
        mockJobStatus.mockResolvedValue({ status: "done" });
        await submitText();
        await settle();

        expect(screen.getByTestId("ingest-empty")).toBeTruthy();
        expect(screen.queryByTestId("ingest-success")).toBeNull();
    });

    test("failed shows the reported error and no success wording", async () => {
        mockJobStatus.mockResolvedValue({
            status: "failed", error: "Provider unavailable", message: "Ingestion failed.",
            memory_ids: [], memory_count: 0,
        });
        await submitText();
        await settle();

        expect(screen.getByTestId("ingest-failed").textContent).toContain("Provider unavailable");
        expect(screen.queryByTestId("ingest-success")).toBeNull();
        expect(screen.queryByText(/Created|complete/i)).toBeNull();
    });

    test("a duplicate reported by the job says so and shows what the API reports", async () => {
        mockJobStatus.mockResolvedValue({
            status: "duplicate", message: "Same content as an earlier document.",
            memory_ids: [ID_A], memory_count: 1,
        });
        await submitText();
        await settle();

        const dup = screen.getByTestId("ingest-duplicate");
        expect(dup.textContent).toContain("This exact text was already submitted");
        expect(dup.textContent).toContain("Same content as an earlier document.");
        expect(screen.getByRole("link", { name: /Open existing memory 1/ }).getAttribute("href")).toBe(`/memories/${ID_A}`);
        expect(screen.queryByTestId("ingest-success")).toBeNull();
    });

    test("an identical earlier submission (already_exists) is not claimed as created", async () => {
        mockCreate.mockResolvedValue({ job_id: "job-0", status: "completed", already_exists: true });
        mockJobStatus.mockResolvedValue({ status: "done", memory_ids: [ID_A], memory_count: 1 });
        await submitText();
        await settle();

        const dup = screen.getByTestId("ingest-duplicate").textContent;
        expect(dup).toContain("This exact text was already submitted — here are the memories from that earlier submission");
        const success = screen.getByTestId("ingest-success").textContent;
        expect(success).not.toMatch(/Created/);
        expect(success).toContain("1 memory from the earlier submission");
        expect(screen.getByTestId("ingest-memory-link-0").getAttribute("href")).toBe(`/memories/${ID_A}`);
    });

    test("already_exists false says Created", async () => {
        mockJobStatus.mockResolvedValue({ status: "done", memory_ids: [ID_A], memory_count: 1 });
        await submitText();
        await settle();

        expect(screen.getByTestId("ingest-success").textContent).toContain("Created 1 memory");
        expect(screen.queryByTestId("ingest-duplicate")).toBeNull();
    });

    test("when the API does not say, the wording is neutral and never claims creation", async () => {
        mockCreate.mockResolvedValue({ job_id: "job-1", status: "queued" });
        mockJobStatus.mockResolvedValue({ status: "done", memory_ids: [ID_A, ID_B], memory_count: 2 });
        await submitText();
        await settle();

        const success = screen.getByTestId("ingest-success").textContent;
        expect(success).toContain("Processed — 2 memories");
        expect(success).not.toMatch(/Created/);
        expect(screen.queryByTestId("ingest-duplicate")).toBeNull();
    });

    test("a duplicate status from the POST with no already_exists still shows the notice", async () => {
        mockCreate.mockResolvedValue({ job_id: "job-1", status: "duplicate", message: "Already submitted earlier." });
        mockJobStatus.mockResolvedValue({ status: "done", memory_ids: [ID_A], memory_count: 1 });
        await submitText();
        await settle();

        expect(screen.getByTestId("ingest-duplicate").textContent).toContain("Already submitted earlier.");
        expect(screen.getByTestId("ingest-success").textContent).not.toMatch(/Created/);
    });
});

describe("long-running jobs", () => {
    test("say it is still processing without declaring failure, then give a non-failure timeout", async () => {
        INGEST_TIMING.slowAfterMs = 250;
        INGEST_TIMING.giveUpAfterMs = 1200;
        mockJobStatus.mockResolvedValue({ status: "embedding" });
        await submitText();

        await waitFor(() => expect(screen.getByTestId("ingest-slow")).toBeTruthy(), { timeout: 2000 });
        expect(screen.getByTestId("ingest-slow").textContent).toMatch(/Still processing/);
        expect(screen.getByTestId("ingest-slow").textContent).toMatch(/leave this page/);
        expect(screen.queryByTestId("ingest-failed")).toBeNull();

        await waitFor(() => expect(screen.getByTestId("ingest-timeout")).toBeTruthy(), { timeout: 3000 });
        expect(screen.getByTestId("ingest-timeout").textContent).toMatch(/longer than expected/);
        expect(screen.queryByTestId("ingest-failed")).toBeNull();
        expect(screen.queryByTestId("ingest-edit-again")).toBeNull();

        // Polling has stopped, and Check again resumes it without resubmitting.
        const stopped = mockJobStatus.mock.calls.length;
        await settle(500);
        expect(mockJobStatus.mock.calls.length).toBe(stopped);

        mockJobStatus.mockResolvedValue({ status: "done", memory_ids: [ID_A], memory_count: 1 });
        await act(async () => { fireEvent.click(screen.getByTestId("ingest-status-retry")); });
        await waitFor(() => expect(screen.getByTestId("ingest-success")).toBeTruthy());
        expect(mockCreate).toHaveBeenCalledTimes(1);
    });
});

describe("a failed status request is not a failed job", () => {
    test("is worded as unknown status with Check again, and never as ingestion failure", async () => {
        mockJobStatus.mockRejectedValue({ status: 503, body: null });
        await submitText();
        await settle();

        const view = screen.getByTestId("ingest-poll-error");
        expect(view.textContent).toMatch(/does not mean ingestion failed/);
        expect(screen.queryByTestId("ingest-failed")).toBeNull();
        expect(screen.queryByTestId("ingest-edit-again")).toBeNull();
        expect(screen.getByTestId("ingest-status-retry")).toBeTruthy();
    });
});

describe("workspace switch and unmount end the poll", () => {
    test("a workspace/identity scope change stops polling and ignores later responses", async () => {
        let release;
        mockJobStatus
            .mockResolvedValueOnce({ status: "extracting" })
            .mockImplementation(() => new Promise((resolve) => { release = resolve; }));
        await submitText();
        await settle(500);

        await act(async () => { resetIdentityScopedCaches(); });
        const before = mockJobStatus.mock.calls.length;
        // The previous workspace's response lands late; it must not render.
        await act(async () => { release({ status: "done", memory_ids: [ID_A], memory_count: 1 }); });
        await settle(600);

        expect(mockJobStatus.mock.calls.length).toBe(before);
        expect(screen.queryByTestId("ingest-success")).toBeNull();
    });

    test("unmount stops polling", async () => {
        mockJobStatus.mockResolvedValue({ status: "chunking" });
        const { unmount } = renderPage();
        await openPanel();
        await typeAndSubmit("text");
        await settle();
        unmount();
        const before = mockJobStatus.mock.calls.length;
        await settle(600);
        expect(mockJobStatus.mock.calls.length).toBe(before);
    });
});

describe("retry controls match what the backend does", () => {
    test("after a failed job the same text is not retryable", async () => {
        mockJobStatus.mockResolvedValue({ status: "failed", error: "Provider unavailable" });
        await submitText("exact text");
        await settle();

        expect(screen.queryByRole("button", { name: /^Retry$/ })).toBeNull();
        expect(screen.queryByTestId("ingest-retry-submit")).toBeNull();

        await act(async () => { fireEvent.click(screen.getByTestId("ingest-edit-again")); });

        // The text stays in the box, but submitting it again is blocked.
        expect(screen.getByTestId("ingest-content").value).toBe("exact text");
        expect(screen.getByTestId("ingest-submit").disabled).toBe(true);
        expect(screen.getByTestId("ingest-same-text-note").textContent).toMatch(/identical text returns the same result/);

        await act(async () => { fireEvent.click(screen.getByTestId("ingest-submit")); });
        expect(mockCreate).toHaveBeenCalledTimes(1);

        // Changing the text re-enables it, and a new submission gets a new key.
        mockJobStatus.mockResolvedValue({ status: "extracting" });
        await act(async () => {
            fireEvent.change(screen.getByTestId("ingest-content"), { target: { value: "exact text, edited" } });
        });
        expect(screen.getByTestId("ingest-submit").disabled).toBe(false);
        expect(screen.queryByTestId("ingest-same-text-note")).toBeNull();
        await act(async () => { fireEvent.click(screen.getByTestId("ingest-submit")); });
        expect(mockCreate).toHaveBeenCalledTimes(2);
        expect(mockCreate.mock.calls[1][0].content).toBe("exact text, edited");
        expect(mockCreate.mock.calls[1][0].idempotencyKey).not.toBe(mockCreate.mock.calls[0][0].idempotencyKey);
    });

    test("whitespace-only edits are the same text: the API trims before hashing", async () => {
        mockJobStatus.mockResolvedValue({ status: "failed", error: "Provider unavailable" });
        await submitText("exact text");
        await settle();
        await act(async () => { fireEvent.click(screen.getByTestId("ingest-edit-again")); });

        for (const variant of ["exact text  ", "\n exact text", "  exact text\n\n"]) {
            await act(async () => {
                fireEvent.change(screen.getByTestId("ingest-content"), { target: { value: variant } });
            });
            expect(screen.getByTestId("ingest-submit").disabled).toBe(true);
            expect(screen.getByTestId("ingest-same-text-note")).toBeTruthy();
        }
        await act(async () => { fireEvent.click(screen.getByTestId("ingest-submit")); });
        expect(mockCreate).toHaveBeenCalledTimes(1);

        await act(async () => {
            fireEvent.change(screen.getByTestId("ingest-content"), { target: { value: "exact text!" } });
        });
        expect(screen.getByTestId("ingest-submit").disabled).toBe(false);
    });

    test("after a zero-memory job the same text is blocked and edit-and-resubmit is offered", async () => {
        mockJobStatus.mockResolvedValue({ status: "done", memory_ids: [], memory_count: 0 });
        await submitText("nothing extractable");
        await settle();

        expect(screen.queryByRole("button", { name: /^Retry$/ })).toBeNull();
        expect(screen.getByTestId("ingest-edit-again").textContent).toBe("Edit text and submit again");

        await act(async () => { fireEvent.click(screen.getByTestId("ingest-edit-again")); });
        expect(screen.getByTestId("ingest-content").value).toBe("nothing extractable");
        expect(screen.getByTestId("ingest-submit").disabled).toBe(true);
    });

    test("after a duplicate the same text is blocked too", async () => {
        mockJobStatus.mockResolvedValue({ status: "duplicate", message: "dup" });
        await submitText("dup text");
        await settle();

        await act(async () => { fireEvent.click(screen.getByTestId("ingest-edit-again")); });
        expect(screen.getByTestId("ingest-submit").disabled).toBe(true);
    });

    test("success offers no retry and Ingest another clears the text", async () => {
        mockJobStatus.mockResolvedValue({ status: "done", memory_ids: [ID_A], memory_count: 1 });
        await submitText("fine text");
        await settle();

        expect(screen.queryByTestId("ingest-edit-again")).toBeNull();
        await act(async () => { fireEvent.click(screen.getByTestId("ingest-reset")); });
        expect(screen.getByTestId("ingest-content").value).toBe("");
        expect(screen.getByTestId("ingest-submit").disabled).toBe(true);
    });

    test.each([
        ["a network error", { status: 0, network: true, body: null }],
        ["a 502", { status: 502, body: null }],
    ])("%s on the POST allows a retry with the SAME key and creates a single job", async (_label, failure) => {
        mockCreate
            .mockRejectedValueOnce(failure)
            .mockResolvedValue({ job_id: "job-1", status: "queued" });
        mockJobStatus.mockResolvedValue({ status: "done", memory_ids: [ID_A], memory_count: 1 });
        await submitText("ambiguous text");
        await settle();

        expect(screen.getByTestId("ingest-submit-unconfirmed").textContent).toMatch(/could not confirm/);
        expect(mockJobStatus).not.toHaveBeenCalled();

        await act(async () => { fireEvent.click(screen.getByTestId("ingest-retry-submit")); });
        await settle();

        expect(mockCreate).toHaveBeenCalledTimes(2);
        expect(mockCreate.mock.calls[1][0].idempotencyKey).toBe(mockCreate.mock.calls[0][0].idempotencyKey);
        expect(screen.getByTestId("ingest-success")).toBeTruthy();
        // One accepted submission, one job polled.
        expect(new Set(mockJobStatus.mock.calls.map((c) => c[0]))).toEqual(new Set(["job-1"]));
    });

    test("a definite rejection (4xx) offers no same-key retry", async () => {
        mockCreate.mockRejectedValueOnce({ status: 422, body: { error: { code: "SM010", message: "bad" } } });
        await submitText("rejected text");
        await settle();

        expect(screen.queryByTestId("ingest-submit-unconfirmed")).toBeNull();
        expect(screen.queryByTestId("ingest-retry-submit")).toBeNull();
    });

    test("editing the text after an ambiguous failure clears the retry and uses a new key", async () => {
        mockCreate
            .mockRejectedValueOnce({ status: 0, network: true })
            .mockResolvedValue({ job_id: "job-2", status: "queued" });
        mockJobStatus.mockResolvedValue({ status: "extracting" });
        await submitText("first");
        await settle();
        expect(screen.getByTestId("ingest-retry-submit")).toBeTruthy();

        await act(async () => {
            fireEvent.change(screen.getByTestId("ingest-content"), { target: { value: "second" } });
        });
        expect(screen.queryByTestId("ingest-retry-submit")).toBeNull();
        await act(async () => { fireEvent.click(screen.getByTestId("ingest-submit")); });
        expect(mockCreate.mock.calls[1][0].idempotencyKey).not.toBe(mockCreate.mock.calls[0][0].idempotencyKey);
    });
});
