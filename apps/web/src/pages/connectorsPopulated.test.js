/**
 * A populated connector list in the shape the real API returns.
 *
 * THE DEFECT
 *
 * The page was written against the demo data, whose connectors carry a
 * `total_artifacts_synced` count, and read it unconditionally:
 *
 *     {c.total_artifacts_synced.toLocaleString()}
 *     list.reduce((a, c) => a + c.total_artifacts_synced, 0)
 *
 * The backend's ConnectorResponse has no such field (id, workspace_id,
 * connector_type, display_name, config, status, last_sync_at, next_sync_at,
 * created_at). With one real connector the card threw a TypeError while
 * rendering, and the subtitle summed to NaN. The empty list never touched the
 * field, which is why nothing caught it.
 *
 * WHAT THESE ASSERT
 *
 * A real-shaped list renders; a missing count reads "unavailable" and is never
 * shown as 0; the subtitle publishes an artifact total only when every
 * connector has a count (a partial sum would be a wrong number); and demo
 * connectors that do carry a count keep showing it. The API is mocked; no
 * network, provider or production call is made.
 *
 * ABOUT THE FIXTURE
 *
 * __fixtures__/connectorList.populated.json is SCHEMA-GENERATED, not a captured
 * live response: it is the backend's own ConnectorListResponse /
 * ConnectorResponse (apps/api/sourcemind/schemas/connector.py) serialized with
 * `model_dump(mode="json")`, so its fields are exactly the real ones. Values
 * are invented. The hand-built connector in connectorsError.test.js includes
 * `total_artifacts_synced`, a field the real API never sends, so it could not
 * have exposed this.
 */

import React from "react";
import { render, screen, act, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import REAL_CONNECTOR_LIST from "./__fixtures__/connectorList.populated.json";

const mockList = jest.fn();

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        listConnectors: (...a) => mockList(...a),
        triggerSync: jest.fn(async () => ({})),
        getSyncLogs: jest.fn(async () => ({ logs: [] })),
        createConnector: jest.fn(async () => ({})),
    },
}));

import Connectors from "./Connectors";

const settle = async (ms = 250) => {
    await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
};

/** What realApi.listConnectors hands the page: the {items} envelope as `connectors`. */
const asRealApi = (r) => ({ ...r, connectors: r.items || r.connectors || [] });

/** A demo-data connector: these DO carry total_artifacts_synced. */
const demoConnector = (id, name, count) => ({
    id,
    source_tool: "github",
    name,
    status: "active",
    last_synced_at: "2026-10-04T09:30:00Z",
    total_artifacts_synced: count,
});

const renderPage = () => render(<MemoryRouter><Connectors /></MemoryRouter>);
const subtitle = () => screen.getByTestId("page-subtitle").textContent;

beforeEach(() => {
    mockList.mockReset();
});

describe("a populated list in the real response shape", () => {
    test("renders every connector instead of throwing", async () => {
        mockList.mockResolvedValue(asRealApi(REAL_CONNECTOR_LIST));

        renderPage();
        await settle();

        for (const item of REAL_CONNECTOR_LIST.items) {
            const card = screen.getByTestId(`connector-${item.id}`);
            expect(card.textContent).toContain(item.display_name);
            expect(card.textContent).toContain(item.connector_type);
            expect(within(card).getByTestId(`status-badge-${item.status}`)).toBeTruthy();
            expect(card.textContent).not.toContain("NaN");
        }
    });

    test("an absent artifact count is unavailable, never zero", async () => {
        mockList.mockResolvedValue(asRealApi(REAL_CONNECTOR_LIST));

        renderPage();
        await settle();

        for (const item of REAL_CONNECTOR_LIST.items) {
            const card = screen.getByTestId(`connector-${item.id}`);
            expect(within(card).getByText("unavailable")).toBeTruthy();
            expect(card.textContent).not.toMatch(/Artifacts synced\s*0(?!\d)/);
        }
        // No total is published, and nothing is NaN.
        expect(subtitle()).toBe(`${REAL_CONNECTOR_LIST.items.length} connected sources`);
    });
});

describe("connectors that carry a count keep showing it", () => {
    test("each card shows its count and the subtitle shows the total", async () => {
        mockList.mockResolvedValue({
            connectors: [
                demoConnector("conn_a", "acme/platform", 12481),
                demoConnector("conn_b", "acme/infra", 3102),
            ],
        });

        renderPage();
        await settle();

        const total = (12481 + 3102).toLocaleString();
        expect(subtitle()).toBe(`2 connected sources · ${total} artifacts synced`);
        expect(within(screen.getByTestId("connector-conn_a")).getByText((12481).toLocaleString())).toBeTruthy();
        expect(within(screen.getByTestId("connector-conn_b")).getByText((3102).toLocaleString())).toBeTruthy();
        expect(screen.queryByText("unavailable")).toBeNull();
    });

    test("a mixed list does not publish a partial total", async () => {
        const [realOne] = REAL_CONNECTOR_LIST.items;
        mockList.mockResolvedValue({
            connectors: [demoConnector("conn_a", "acme/platform", 5), realOne],
        });

        renderPage();
        await settle();

        expect(subtitle()).toBe("2 connected sources");
        expect(within(screen.getByTestId("connector-conn_a")).getByText("5")).toBeTruthy();
        expect(within(screen.getByTestId(`connector-${realOne.id}`)).getByText("unavailable")).toBeTruthy();
    });
});
