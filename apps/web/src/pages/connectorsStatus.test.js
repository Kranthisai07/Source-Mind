/**
 * Honest connector status: what each scope status and run state says, the
 * polling lifecycle, and the row actions.
 *
 * Fixtures are SCHEMA-GENERATED from the backend's ConnectorStatusResponse
 * (__fixtures__/connectorStatus.variants.json). The API is mocked; no network.
 */

import React from "react";
import { render, screen, act, within, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import LIST from "./__fixtures__/connectorList.populated.json";
import VARIANTS from "./__fixtures__/connectorStatus.variants.json";

const mockList = jest.fn();
const mockStatus = jest.fn();
const mockSync = jest.fn();
const mockRetry = jest.fn();
const mockRescan = jest.fn();
const mockUpdate = jest.fn();

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        listConnectors: (...a) => mockList(...a),
        getConnectorStatus: (...a) => mockStatus(...a),
        triggerSync: (...a) => mockSync(...a),
        retryFailedConnector: (...a) => mockRetry(...a),
        rescanConnector: (...a) => mockRescan(...a),
        updateConnector: (...a) => mockUpdate(...a),
        getSyncLogs: jest.fn(async () => ({ items: [], total: 0 })),
    },
}));

import Connectors from "./Connectors";
import { POLL_INTERVAL_MS } from "../components/connectors/ConnectorCard";

const ONE = { ...LIST, items: [LIST.items[0]], connectors: [LIST.items[0]], total: 1 };
const ID = LIST.items[0].id;

const flush = async (ms = 0) => {
    await act(async () => { jest.advanceTimersByTime(ms); });
    await act(async () => { await Promise.resolve(); });
};

const renderPage = () => render(<MemoryRouter><Connectors /></MemoryRouter>);

beforeEach(() => {
    jest.useFakeTimers();
    mockList.mockReset().mockResolvedValue(ONE);
    mockStatus.mockReset().mockResolvedValue(VARIANTS.idle);
    mockSync.mockReset().mockResolvedValue({ task_id: "t", connector_id: ID, sync_type: "incremental", message: "queued" });
    mockRetry.mockReset().mockResolvedValue({ connector_id: ID, eligible: 2, requeued: 2, refused: 0, remaining: 0 });
    mockRescan.mockReset().mockResolvedValue({ connector_id: ID, scopes_marked: 2, message: "ok" });
    mockUpdate.mockReset().mockResolvedValue({});
});

afterEach(() => {
    jest.useRealTimers();
});

describe("scope statuses are explained in plain language", () => {
    test("each scope status has its own wording, and a denial reads differently from an unverifiable state", async () => {
        mockStatus.mockResolvedValue(VARIANTS.needs_attention);
        renderPage();
        await flush();

        const row = (kind) => screen.getByTestId(`scope-acme/platform-${kind}`).textContent;
        const unverified = row("commits");
        const reauth = row("issues");
        const lost = row("pull_requests");
        const errored = row("issue_comments");

        expect(unverified).toMatch(/could not confirm your GitHub access/i);
        expect(unverified).toMatch(/nothing is imported/i);
        expect(unverified).toMatch(/not a denial/i);
        expect(reauth).toMatch(/authorize SourceMind again/i);
        expect(lost).toMatch(/access to this repository is gone/i);
        expect(lost).not.toMatch(/could not confirm/i);
        expect(errored).toMatch(/failed/i);
        // All four are different texts.
        expect(new Set([unverified, reauth, lost, errored]).size).toBe(4);
        expect(screen.getByTestId(`connector-state-${ID}`).textContent).toMatch(/needs attention/i);
    });

    test("the connector-level error code is mapped, never shown as a raw backend message", async () => {
        mockStatus.mockResolvedValue(VARIANTS.needs_attention);
        renderPage();
        await flush();

        expect(screen.getByTestId(`connector-error-${ID}`).textContent).toMatch(/authorize SourceMind again/i);
    });
});

describe("run state", () => {
    test("a running sync shows its counters and blocks a second Sync now", async () => {
        mockStatus.mockResolvedValue(VARIANTS.syncing);
        renderPage();
        await flush();

        const progress = screen.getByTestId(`sync-progress-${ID}`).textContent;
        expect(progress).toContain("found 120");
        expect(progress).toContain("new 30");
        expect(progress).toContain("failed 1");
        const button = screen.getByTestId(`sync-now-${ID}`);
        expect(button.disabled).toBe(true);
        expect(button.textContent).toMatch(/Syncing/);
        expect(screen.getByTestId(`connector-state-${ID}`).textContent).toMatch(/syncing/i);
    });

    test("a paused connector can be resumed and cannot be synced", async () => {
        mockList.mockResolvedValue({ ...ONE, connectors: [{ ...LIST.items[1] }] });
        mockStatus.mockResolvedValue(VARIANTS.paused);
        renderPage();
        await flush();

        const id = LIST.items[1].id;
        expect(screen.getByTestId(`sync-now-${id}`).disabled).toBe(true);
        expect(screen.getByTestId(`pause-${id}`).textContent).toMatch(/Resume/);
        expect(screen.getByTestId(`next-sync-${id}`).textContent).toBe("not scheduled");
    });
});

describe("polling", () => {
    test("polls on an interval while syncing, stops when the run ends", async () => {
        mockStatus.mockResolvedValueOnce(VARIANTS.syncing).mockResolvedValue(VARIANTS.idle);
        renderPage();
        await flush();
        expect(mockStatus).toHaveBeenCalledTimes(1);

        await flush(POLL_INTERVAL_MS);
        expect(mockStatus).toHaveBeenCalledTimes(2);
        expect(screen.getByTestId(`connector-state-${ID}`).textContent).toMatch(/up to date/i);

        await flush(POLL_INTERVAL_MS * 5);
        expect(mockStatus).toHaveBeenCalledTimes(2);
    });

    test("an idle connector is never polled", async () => {
        renderPage();
        await flush();
        await flush(POLL_INTERVAL_MS * 5);
        expect(mockStatus).toHaveBeenCalledTimes(1);
    });

    test("no request is made after unmount", async () => {
        mockStatus.mockResolvedValue(VARIANTS.syncing);
        const { unmount } = renderPage();
        await flush();
        expect(mockStatus).toHaveBeenCalledTimes(1);

        unmount();
        const before = mockStatus.mock.calls.length;
        await flush(POLL_INTERVAL_MS * 5);
        expect(mockStatus.mock.calls.length).toBe(before);
    });

    test("polling is not a tight loop: one request per interval at most", async () => {
        mockStatus.mockResolvedValue(VARIANTS.syncing);
        renderPage();
        await flush();
        await flush(POLL_INTERVAL_MS - 1);
        expect(mockStatus).toHaveBeenCalledTimes(1);
        await flush(1);
        expect(mockStatus).toHaveBeenCalledTimes(2);
    });

    test("a failed read keeps the last status and keeps trying at the same pace", async () => {
        mockStatus.mockResolvedValueOnce(VARIANTS.syncing).mockRejectedValue({ status: 503, body: null });
        renderPage();
        await flush();
        await flush(POLL_INTERVAL_MS);

        expect(screen.getByTestId(`status-stale-${ID}`).textContent).toMatch(/out of date/i);
        expect(screen.getByTestId(`sync-progress-${ID}`)).toBeTruthy();
        await flush(POLL_INTERVAL_MS);
        expect(mockStatus).toHaveBeenCalledTimes(3);
    });
});

describe("row actions", () => {
    test("Sync now queues once, watches for the run, and refreshes status", async () => {
        renderPage();
        await flush();
        const before = mockStatus.mock.calls.length;

        await act(async () => { fireEvent.click(screen.getByTestId(`sync-now-${ID}`)); });
        await flush();

        expect(mockSync).toHaveBeenCalledTimes(1);
        expect(mockSync).toHaveBeenCalledWith(ID);
        expect(mockStatus.mock.calls.length).toBeGreaterThan(before);
        // Between the request and the run appearing, a second click is blocked.
        expect(screen.getByTestId(`sync-now-${ID}`).disabled).toBe(true);
    });

    test("a refused sync shows why and does not claim success", async () => {
        mockSync.mockRejectedValue({ status: 409, body: { error: { code: "SM010", details: { reason: "connector_paused" } } } });
        renderPage();
        await flush();

        await act(async () => { fireEvent.click(screen.getByTestId(`sync-now-${ID}`)); });
        await flush();

        expect(screen.getByTestId(`action-error-${ID}`).textContent).toMatch(/paused/i);
        expect(screen.getByTestId(`sync-now-${ID}`).disabled).toBe(false);
    });

    test("a rate limit is reported as such", async () => {
        mockSync.mockRejectedValue({ status: 429, retryAfterSeconds: 30, body: null });
        renderPage();
        await flush();

        await act(async () => { fireEvent.click(screen.getByTestId(`sync-now-${ID}`)); });
        await flush();

        expect(screen.getByTestId(`action-error-${ID}`).textContent).toMatch(/Too many requests.*30s/);
    });

    test("Retry failed appears only when something failed, and calls the endpoint", async () => {
        renderPage();
        await flush();

        await act(async () => { fireEvent.click(screen.getByTestId(`retry-failed-${ID}`)); });
        await flush();
        expect(mockRetry).toHaveBeenCalledWith(ID);
    });

    test("no Retry failed when nothing failed", async () => {
        mockStatus.mockResolvedValue({ ...VARIANTS.idle, documents: { ...VARIANTS.idle.documents, failed: 0 } });
        renderPage();
        await flush();
        expect(screen.queryByTestId(`retry-failed-${ID}`)).toBeNull();
    });

    test("Re-scan and Pause call their endpoints", async () => {
        renderPage();
        await flush();

        await act(async () => { fireEvent.click(screen.getByTestId(`rescan-${ID}`)); });
        await flush();
        expect(mockRescan).toHaveBeenCalledWith(ID);

        await act(async () => { fireEvent.click(screen.getByTestId(`pause-${ID}`)); });
        await flush();
        expect(mockUpdate).toHaveBeenCalledWith(ID, { status: "paused" });
    });

    test("scope table is readable inside the card", async () => {
        mockStatus.mockResolvedValue(VARIANTS.needs_attention);
        renderPage();
        await flush();
        const table = within(screen.getByTestId(`connector-${ID}`)).getByTestId(`scope-table-${ID}`);
        expect(table.textContent).toContain("pull requests");
    });
});
