/**
 * A populated connector list in the shape the real API returns.
 *
 * History: the page once read `total_artifacts_synced`, a demo-only field the
 * real ConnectorResponse never carries, and threw while rendering the first
 * real connector. The page no longer reads any demo-only field. Counts now come
 * from GET /connectors/{id}/status (`documents`), and an unavailable status is
 * shown as unavailable, never as zero.
 *
 * __fixtures__/connectorList.populated.json is SCHEMA-GENERATED from
 * apps/api/sourcemind/schemas/connector.py (ConnectorListResponse), not a
 * captured live response. Values are invented. The API is mocked.
 */

import React from "react";
import { render, screen, act, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import REAL_CONNECTOR_LIST from "./__fixtures__/connectorList.populated.json";
import STATUS_VARIANTS from "./__fixtures__/connectorStatus.variants.json";

const mockList = jest.fn();
const mockStatus = jest.fn();

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        listConnectors: (...a) => mockList(...a),
        getConnectorStatus: (...a) => mockStatus(...a),
        triggerSync: jest.fn(async () => ({})),
        getSyncLogs: jest.fn(async () => ({ items: [], total: 0 })),
    },
}));

import Connectors from "./Connectors";

const settle = async (ms = 100) => {
    await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
};

/** What realApi.listConnectors hands the page: the {items} envelope as `connectors`. */
const asRealApi = (r) => ({ ...r, connectors: r.items || r.connectors || [] });

const renderPage = () => render(<MemoryRouter><Connectors /></MemoryRouter>);
const subtitle = () => screen.getByTestId("page-subtitle").textContent;

beforeEach(() => {
    mockList.mockReset();
    mockStatus.mockReset().mockRejectedValue({ status: 503, body: null });
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
            expect(card.textContent).not.toContain("undefined");
        }
        expect(subtitle()).toBe(`${REAL_CONNECTOR_LIST.items.length} connected sources`);
    });

    test("when status cannot be read, the card says so and shows no counts", async () => {
        mockList.mockResolvedValue(asRealApi(REAL_CONNECTOR_LIST));

        renderPage();
        await settle();

        const [first] = REAL_CONNECTOR_LIST.items;
        const card = screen.getByTestId(`connector-${first.id}`);
        expect(within(card).getByTestId(`status-stale-${first.id}`).textContent).toMatch(/unavailable/i);
        expect(screen.queryByTestId(`doc-counts-${first.id}`)).toBeNull();
    });

    test("a connector type that cannot sync is listed without sync controls", async () => {
        mockList.mockResolvedValue(asRealApi(REAL_CONNECTOR_LIST));

        renderPage();
        await settle();

        const discord = REAL_CONNECTOR_LIST.items.find((c) => c.connector_type === "discord");
        expect(screen.getByTestId(`connector-unsupported-${discord.id}`)).toBeTruthy();
        expect(screen.queryByTestId(`sync-now-${discord.id}`)).toBeNull();
    });
});

describe("status from the real shape", () => {
    test("document counters and filtered counts render with their real numbers", async () => {
        const item = REAL_CONNECTOR_LIST.items[0];
        mockList.mockResolvedValue(asRealApi(REAL_CONNECTOR_LIST));
        mockStatus.mockResolvedValue(STATUS_VARIANTS.idle);

        renderPage();
        await settle();

        const card = screen.getByTestId(`connector-${item.id}`);
        expect(within(card).getByTestId(`count-failed-${item.id}`).textContent).toBe("2");
        expect(within(card).getByTestId(`count-quarantined-${item.id}`).textContent).toBe("1");
        const counts = within(card).getByTestId(`doc-counts-${item.id}`).textContent;
        expect(counts).toContain("310");
        expect(counts).toContain("Zero facts4");
        expect(within(card).getByTestId(`filtered-${item.id}`).textContent).toMatch(/17 bot-authored, 3 empty body/);
        expect(card.textContent).not.toContain("NaN");
    });
});
