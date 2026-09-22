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

describe("under React Strict Mode", () => {
    /*
     * index.js wraps App in <React.StrictMode>, which in development mounts,
     * runs cleanup, and mounts again. A cleanup that tears something down
     * without setup rebuilding it is therefore permanently torn down.
     *
     * The `alive` ref guarding this page's responses had exactly that shape —
     * set false by the simulated unmount and never set back — so every
     * response was discarded and the page rendered "0 connected sources"
     * forever. That is the same failure the error handling was added to
     * prevent, reintroduced by the guard meant to protect it.
     *
     * The other suites render without StrictMode and cannot see this, which is
     * why it needs its own cases.
     */
    const renderStrict = () =>
        render(
            <React.StrictMode>
                <MemoryRouter><Connectors /></MemoryRouter>
            </React.StrictMode>
        );

    test("connectors still render after the double mount", async () => {
        mockList.mockResolvedValue({ connectors: [connector("c-1", "acme/platform")] });

        renderStrict();
        await settle();

        await waitFor(() => expect(screen.getByTestId("connector-c-1")).toBeTruthy());
        expect(screen.getByText(/1 connected sources/)).toBeTruthy();
    });

    test("an error still surfaces after the double mount", async () => {
        mockList.mockRejectedValue({ status: 503, body: null });

        renderStrict();
        await settle();

        await waitFor(() => expect(screen.getByTestId("connectors-error")).toBeTruthy());
        expect(screen.queryByText(/0 connected sources/)).toBeNull();
    });

    test("retry still works after the double mount", async () => {
        // Strict Mode invokes the effect twice, so BOTH initial loads must
        // reject — `mockRejectedValueOnce` would leave the second call with no
        // implementation and fail for a reason that has nothing to do with
        // retry.
        mockList.mockRejectedValue({ status: 503, body: null });

        renderStrict();
        await settle();
        await waitFor(() => expect(screen.getByTestId("connectors-error")).toBeTruthy());

        mockList.mockResolvedValue({ connectors: [connector("c-2", "acme/api")] });
        await act(async () => {
            fireEvent.click(screen.getByTestId("error-retry"));
        });
        await settle();

        await waitFor(() => expect(screen.getByTestId("connector-c-2")).toBeTruthy());
    });

    test("a real unmount still discards a late response", async () => {
        // The protection must survive the fix: rebuilding `alive` on setup
        // must not stop a genuine unmount from rejecting what lands after it.
        let resolveLate;
        mockList.mockImplementation(() => new Promise((res) => { resolveLate = res; }));

        const { unmount } = renderStrict();
        await settle();
        unmount();

        await act(async () => { resolveLate({ connectors: [connector("c-late", "late/repo")] }); });
        await settle();

        expect(screen.queryByTestId("connector-c-late")).toBeNull();
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
