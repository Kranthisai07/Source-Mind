/**
 * What an ingestion job's polled status means to the person who submitted it.
 *
 * The real API reports no stage list and no elapsed time. It reports a
 * `status` (the worker's current stage, with `completed` rewritten to `done`
 * by realApi), plus `memory_ids`, `memory_count`, `message` and `error`.
 * A job can finish without error and still have produced nothing searchable,
 * so "done" alone never means "success": success is `memory_count > 0`.
 *
 * Pure functions, no React, so each branch is testable on its own.
 */

/** Mutable on purpose: tests shorten the clock-based thresholds. */
export const INGEST_TIMING = {
    pollBaseMs: 180,
    pollGrowth: 1.3,
    pollMaxMs: 2000,
    slowAfterMs: 60 * 1000,
    giveUpAfterMs: 10 * 60 * 1000,
};

/** Delay before poll number `attempt` (0-based): backs off, capped. */
export function pollDelayMs(attempt) {
    const { pollBaseMs, pollGrowth, pollMaxMs } = INGEST_TIMING;
    return Math.min(Math.round(pollBaseMs * pollGrowth ** attempt), pollMaxMs);
}

// Honest labels for the worker's current status. These are what the backend
// said, not a pipeline diagram: there is no stage list to draw.
const STATUS_LABELS = {
    pending: "Waiting to start",
    queued: "Queued",
    processing: "Processing",
    extracting: "Reading the text",
    chunking: "Splitting the text into passages",
    extracting_facts: "Extracting facts",
    embedding: "Preparing facts for search",
    attributing: "Working out who contributed",
    indexing: "Saving memories",
    retrying: "A temporary error occurred; retrying automatically",
};

export function statusLabel(status) {
    return STATUS_LABELS[status] ?? "Processing";
}

/** Count of memories the job reports, from whichever field is present. */
export function createdCount(job) {
    const ids = Array.isArray(job?.memory_ids) ? job.memory_ids.length : 0;
    const count = typeof job?.memory_count === "number" ? job.memory_count : 0;
    return Math.max(ids, count);
}

/**
 * @returns {{kind: "progress"|"retrying"|"success"|"empty"|"failed"|"duplicate",
 *            terminal: boolean, count: number, ids: string[]}}
 */
export function ingestOutcome(job) {
    const ids = Array.isArray(job?.memory_ids) ? job.memory_ids.filter(Boolean) : [];
    const count = createdCount(job);
    const status = job?.status;
    if (status === "failed") return { kind: "failed", terminal: true, count, ids };
    if (status === "duplicate") return { kind: "duplicate", terminal: true, count, ids };
    if (status === "done" || status === "completed") {
        return { kind: count > 0 ? "success" : "empty", terminal: true, count, ids };
    }
    if (status === "retrying") return { kind: "retrying", terminal: false, count, ids };
    return { kind: "progress", terminal: false, count, ids };
}

/**
 * A POST that may or may not have been received: no job id came back, and
 * either nothing was reached or the server failed. Only these are safe to
 * retry with the SAME idempotency key.
 */
export function isAmbiguousSubmitFailure(error) {
    if (!error) return false;
    if (error.network === true) return true;
    const status = typeof error.status === "number" ? error.status : null;
    return status === 0 || (status !== null && status >= 500);
}
