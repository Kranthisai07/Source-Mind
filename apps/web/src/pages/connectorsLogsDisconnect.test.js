/**
 * Sync-log drawer (real SyncLogsResponse shape) and the Disconnect dialog.
 * Fixtures are SCHEMA-GENERATED from the backend (see __fixtures__). Mocked API.
 */

import React from "react";
import { render, screen, act, fireEvent, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import LIST from "./__fixtures__/connectorList.populated.json";
import LOGS from "./__fixtures__/syncLogs.real.json";
import DISCONNECT from "./__fixtures__/disconnect.real.json";

const mockList = jest.fn();
const mockLogs = jest.fn();
const mockDelete = jest.fn();

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        listConnectors: (...a) => mockList(...a),
        getConnectorStatus: jest.fn(async () => { throw { status: 503, body: null }; }),
        getSyncLogs: (...a) => mockLogs(...a),
        deleteConnector: (...a) => mockDelete(...a),
    },
}));

import Connectors from "./Connectors";

const ONE = { ...LIST, items: [LIST.items[0]], connectors: [LIST.items[0]], total: 1 };
const ID = LIST.items[0].id;

const settle = async (ms = 80) => {
    await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
};

const openLogs = async () => {
    render(<MemoryRouter><Connectors /></MemoryRouter>);
    await settle();
    await act(async () => { fireEvent.click(screen.getByTestId(`view-logs-${ID}`)); });
    await settle();
};

beforeEach(() => {
    mockList.mockReset().mockResolvedValue(ONE);
    mockLogs.mockReset().mockResolvedValue(LOGS);
    mockDelete.mockReset().mockResolvedValue(DISCONNECT);
});

describe("sync log drawer", () => {
    test("renders the real shape: items, counters, trigger, duration", async () => {
        await openLogs();
        const row = screen.getByTestId("sync-log-row-0").textContent;
        expect(row).toContain("scheduled");
        expect(row).toContain("44"); // found
        expect(row).toContain("40"); // new
        expect(row).toMatch(/1m 0s|60s|1m/); // completed_at - started_at is one minute
        expect(screen.getByTestId("sync-log-detail-0").textContent).toMatch(/2 bot-authored/);
    });

    test("shows the mapped error_code, not the backend's generic text", async () => {
        await openLogs();
        const detail = screen.getByTestId("sync-log-detail-1").textContent;
        expect(detail).toMatch(/GitHub rate limit reached/);
        expect(detail).toContain("rate_limited");
        expect(document.body.textContent).not.toContain("Connector sync failed.");
    });

    test("rows with missing fields do not crash", async () => {
        mockLogs.mockResolvedValue({ items: [{ id: "x", status: "failed" }, {}, null], total: 3 });
        await openLogs();
        expect(screen.getByTestId("sync-log-row-0")).toBeTruthy();
        expect(screen.getByTestId("sync-log-row-1")).toBeTruthy();
        expect(screen.getByTestId("sync-log-detail-0").textContent).toMatch(/no error code recorded/i);
        expect(document.body.textContent).not.toContain("NaN");
        expect(document.body.textContent).not.toContain("Invalid Date");
    });

    test("a response without items is an empty history, and a failure is an error", async () => {
        mockLogs.mockResolvedValue({ logs: [{ date: "x" }] });
        await openLogs();
        expect(screen.getByTestId("sync-logs-empty")).toBeTruthy();
    });

    test("a failed load says so instead of showing an empty history", async () => {
        mockLogs.mockRejectedValue({ status: 503, body: null });
        await openLogs();
        expect(screen.getByTestId("sync-logs-error")).toBeTruthy();
        expect(screen.queryByTestId("sync-logs-empty")).toBeNull();
    });
});

describe("disconnect dialog", () => {
    const openDialog = async () => {
        render(<MemoryRouter><Connectors /></MemoryRouter>);
        await settle();
        await act(async () => { fireEvent.click(screen.getByTestId(`disconnect-${ID}`)); });
        await settle();
    };

    test("states both outcomes in plain words and preselects neither", async () => {
        await openDialog();
        const dialog = screen.getByTestId("disconnect-dialog");
        expect(dialog.textContent).toMatch(/Keep imported memories/);
        expect(dialog.textContent).toMatch(/They stay searchable/);
        expect(dialog.textContent).toMatch(/Remove imported memories/);
        expect(dialog.textContent).toMatch(/nobody has edited are removed from search/);
        expect(dialog.textContent).toMatch(/edited by hand stay searchable/);
        expect(screen.getByTestId("disconnect-option-keep").checked).toBe(false);
        expect(screen.getByTestId("disconnect-option-remove").checked).toBe(false);
        expect(screen.getByTestId("disconnect-confirm").disabled).toBe(true);
    });

    test("Keep sends remove_memories:false", async () => {
        await openDialog();
        fireEvent.click(screen.getByTestId("disconnect-option-keep"));
        await act(async () => { fireEvent.click(screen.getByTestId("disconnect-confirm")); });
        await settle();
        expect(mockDelete).toHaveBeenCalledWith(ID, { remove_memories: false });
        expect(screen.queryByTestId("disconnect-dialog")).toBeNull();
    });

    test("Remove sends remove_memories:true", async () => {
        await openDialog();
        fireEvent.click(screen.getByTestId("disconnect-option-remove"));
        await act(async () => { fireEvent.click(screen.getByTestId("disconnect-confirm")); });
        await settle();
        expect(mockDelete).toHaveBeenCalledWith(ID, { remove_memories: true });
    });

    test("a failure keeps the dialog open and says so", async () => {
        mockDelete.mockRejectedValue({ status: 500, body: null });
        await openDialog();
        fireEvent.click(screen.getByTestId("disconnect-option-keep"));
        await act(async () => { fireEvent.click(screen.getByTestId("disconnect-confirm")); });
        await settle();
        const dialog = screen.getByTestId("disconnect-dialog");
        expect(within(dialog).getByTestId("disconnect-error").textContent).toMatch(/could not complete/i);
    });
});
