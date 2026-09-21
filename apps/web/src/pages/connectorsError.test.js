/**
 * Connectors load failures.
 *
 * THE DEFECT
 *
 *     const load = () => api.listConnectors().then(r => setList(r.connectors));
 *     useEffect(() => { load(); }, []);
 *
 * No catch, no error state, no retry. A failed load rejected into nothing and
 * left `list` at its initial `[]`, so the page rendered "0 connected sources"
 * — the same screen a user with no integrations sees. An outage was therefore
 * indistinguishable from "your connectors are gone", which is the worst way to
 * be wrong about this particular screen.
 *
 * Retry then introduces a second concurrent load, so ordering matters too:
 * these assert which response WINS, positively, rather than checking that no
 * React warning appeared. An absent warning is not evidence of correct
 * ordering — a stale response can overwrite fresh state in perfect silence.
 */

import React from "react";
import { render, screen, act, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const mockList = jest.fn();

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        listConnectors: (...a) => mockList(...a),
        triggerSync: jest.fn(async () => ({})),
        getConnectorLogs: jest.fn(async () => ({ logs: [] })),
        createConnector: jest.fn(async () => ({})),
    },
}));

import Connectors from "./Connectors";

const settle = async (ms = 250) => {
    await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
};

const connector = (id, name) => ({
    id,
    connector_type: "github",
    display_name: name,
    status: "active",
    total_artifacts_synced: 3,
    last_sync_at: null,
});

const renderPage = () => render(<MemoryRouter><Connectors /></MemoryRouter>);

beforeEach(() => {
    mockList.mockReset();
});

describe("a failed load is reported, not rendered as emptiness", () => {
    test("an outage shows an error", async () => {
        mockList.mockRejectedValue({ status: 503, body: null });

        renderPage();
        await settle();

        await waitFor(() => expect(screen.getByTestId("connectors-error")).toBeTruthy());
    });

    test("an outage does NOT claim zero connected sources", async () => {
        mockList.mockRejectedValue({ status: 503, body: null });

        renderPage();
        await settle();

        await waitFor(() => expect(screen.getByTestId("connectors-error")).toBeTruthy());
        // The exact sentence a user with no integrations sees. Saying it while
        // the request failed is a false statement about their account.
        expect(screen.queryByText(/0 connected sources/)).toBeNull();
    });

    test("a 403 is reported too, not swallowed", async () => {
        mockList.mockRejectedValue({ status: 403, body: null });

        renderPage();
        await settle();

        await waitFor(() => expect(screen.getByTestId("connectors-error")).toBeTruthy());
    });
});

describe("retry actually retries", () => {
    test("the retry control issues a new request and recovers", async () => {
        mockList.mockRejectedValueOnce({ status: 503, body: null });

        renderPage();
        await settle();
        await waitFor(() => expect(screen.getByTestId("connectors-error")).toBeTruthy());

        const before = mockList.mock.calls.length;
        mockList.mockResolvedValue({ connectors: [connector("c-1", "acme/platform")] });

        await act(async () => {
            fireEvent.click(screen.getByTestId("error-retry"));
        });
        await settle();

        expect(mockList.mock.calls.length).toBeGreaterThan(before);
        await waitFor(() => expect(screen.getByTestId("connector-c-1")).toBeTruthy());
        // Recovery clears the error rather than stacking it under the data.
        expect(screen.queryByTestId("connectors-error")).toBeNull();
    });
});

describe("ordering, asserted positively", () => {
    test("an older in-flight load cannot overwrite a newer completed one", async () => {
        // Two retries overlap. The SECOND answers first with fresh data; the
        // FIRST then answers late with stale data. Without a generation guard
        // the stale rows win simply by arriving last — silently, with no React
        // warning to notice, which is why this asserts the rendered rows
        // rather than the absence of a warning.
        mockList.mockRejectedValueOnce({ status: 503, body: null });

        renderPage();
        await settle();
        await waitFor(() => expect(screen.getByTestId("connectors-error")).toBeTruthy());

        let resolveStale;
        mockList.mockImplementationOnce(() => new Promise((res) => { resolveStale = res; }));
        await act(async () => {
            fireEvent.click(screen.getByTestId("error-retry"));
        });

        // Second retry, which answers immediately.
        mockList.mockResolvedValueOnce({ connectors: [connector("c-new", "fresh/repo")] });
        await act(async () => {
            fireEvent.click(screen.getByTestId("error-retry"));
        });
        await settle();
        await waitFor(() => expect(screen.getByTestId("connector-c-new")).toBeTruthy());

        // Now the abandoned first retry finally lands.
        await act(async () => { resolveStale({ connectors: [connector("c-old", "stale/repo")] }); });
        await settle();

        expect(screen.getByTestId("connector-c-new")).toBeTruthy();
        expect(screen.queryByTestId("connector-c-old")).toBeNull();
    });

    test("a late failure from an abandoned load does not erase good data", async () => {
        // Same shape, failure edition: an abandoned request rejecting later
        // must not replace rendered connectors with an error screen.
        mockList.mockRejectedValueOnce({ status: 503, body: null });

        renderPage();
        await settle();
        await waitFor(() => expect(screen.getByTestId("connectors-error")).toBeTruthy());

        let rejectStale;
        mockList.mockImplementationOnce(() => new Promise((_res, rej) => { rejectStale = rej; }));
        await act(async () => {
            fireEvent.click(screen.getByTestId("error-retry"));
        });

        mockList.mockResolvedValueOnce({ connectors: [connector("c-new", "fresh/repo")] });
        await act(async () => {
            fireEvent.click(screen.getByTestId("error-retry"));
        });
        await settle();
        await waitFor(() => expect(screen.getByTestId("connector-c-new")).toBeTruthy());

        await act(async () => { rejectStale({ status: 503, body: null }); });
        await settle();

        expect(screen.getByTestId("connector-c-new")).toBeTruthy();
        expect(screen.queryByTestId("connectors-error")).toBeNull();
    });
});

describe("the working path is unchanged", () => {
    test("connectors render when the load succeeds", async () => {
        mockList.mockResolvedValue({
            connectors: [connector("c-1", "acme/platform"), connector("c-2", "acme/api")],
        });

        renderPage();
        await settle();

        await waitFor(() => expect(screen.getByTestId("connector-c-1")).toBeTruthy());
        expect(screen.getByTestId("connector-c-2")).toBeTruthy();
        expect(screen.queryByTestId("connectors-error")).toBeNull();
        expect(screen.getByText(/2 connected sources/)).toBeTruthy();
    });
});
