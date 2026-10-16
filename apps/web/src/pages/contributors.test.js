import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import VARIANTS from "./__fixtures__/githubInsights.variants.json";

const mockGetInsights = jest.fn();

jest.mock("../lib/api", () => ({
    __esModule: true,
    useMocks: true,
    default: {
        getGithubInsights: (...args) => mockGetInsights(...args),
    },
}));

jest.mock("@/components/auth/ClerkAuthBridge", () => ({
    ClerkTokenBridge: () => null,
    RequireAuth: ({ children }) => children,
}));

jest.mock("@/components/layout/AppLayout", () => {
    const ReactModule = require("react");
    const { Outlet } = require("react-router-dom");
    return {
        __esModule: true,
        default: () => ReactModule.createElement("main", null, ReactModule.createElement(Outlet)),
    };
});

jest.mock("@/pages/Memories", () => {
    const ReactModule = require("react");
    return {
        __esModule: true,
        default: () => ReactModule.createElement("div", { "data-testid": "pilot-memories" }, "Memories"),
    };
});

jest.mock("react-markdown", () => ({
    __esModule: true,
    default: ({ children }) => children ?? null,
}));
jest.mock("remark-gfm", () => ({ __esModule: true, default: () => {} }));

import Contributors from "./Contributors";
import App from "../App";
import ConnectorCard from "../components/connectors/ConnectorCard";

const CONNECTOR_ID = "22222222-2222-4222-8222-222222222222";

function renderPage() {
    return render(
        <MemoryRouter initialEntries={[`/connectors/${CONNECTOR_ID}/contributors`]}>
            <Routes>
                <Route path="/connectors/:id/contributors" element={<Contributors />} />
            </Routes>
        </MemoryRouter>
    );
}

beforeEach(() => {
    delete process.env.REACT_APP_PILOT_MODE;
    mockGetInsights.mockReset().mockResolvedValue(VARIANTS.populated);
});

afterEach(() => {
    delete process.env.REACT_APP_PILOT_MODE;
    window.history.pushState({}, "", "/");
});

test("renders imported contributor activity and linked member counts", async () => {
    renderPage();

    const table = await screen.findByTestId("contributors-table");
    expect(screen.getByText("Alice Chen")).toBeTruthy();
    expect(within(screen.getByTestId("contributor-alice-dev")).getByText("8")).toBeTruthy();
    expect(within(screen.getByTestId("contributor-alice-dev")).getByText("3")).toBeTruthy();
    expect(within(table).getByText("bob-builds")).toBeTruthy();

    const link = screen.getByRole("link", { name: "alice-dev" });
    expect(link.getAttribute("href")).toBe("https://github.com/alice-dev");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
});

test("changing the window to 90 days re-queries the connector", async () => {
    renderPage();
    await screen.findByTestId("contributors-table");

    fireEvent.change(screen.getByTestId("window-selector"), { target: { value: "90" } });
    await waitFor(() => expect(mockGetInsights).toHaveBeenCalledTimes(2));

    const [workspaceId, params] = mockGetInsights.mock.calls[1];
    expect(workspaceId).toBeUndefined();
    expect(params.connector_id).toBe(CONNECTOR_ID);
    expect(params.include_disconnected).toBe(false);
    expect(Date.parse(params.until) - Date.parse(params.since)).toBe(90 * 24 * 60 * 60 * 1000);
});

test("the type filter shows only contributors with that activity", async () => {
    renderPage();
    await screen.findByTestId("contributors-table");

    fireEvent.change(screen.getByTestId("type-filter"), { target: { value: "pull_requests" } });

    expect(screen.getByTestId("contributor-alice-dev")).toBeTruthy();
    expect(screen.getByTestId("contributor-bob-builds")).toBeTruthy();
    expect(screen.queryByTestId("contributor-pairing-bot")).toBeNull();
    expect(screen.queryByText("Unresolved author: Sam Local")).toBeNull();
});

test("renders the honest empty state", async () => {
    mockGetInsights.mockResolvedValue(VARIANTS.empty);
    renderPage();

    expect(await screen.findByText("No imported GitHub activity in this window")).toBeTruthy();
    expect(screen.queryByTestId("contributors-table")).toBeNull();
});

test("renders an error instead of a table or fabricated empty counts", async () => {
    mockGetInsights.mockRejectedValue({ status: 503, body: null });
    renderPage();

    expect(await screen.findByTestId("contributors-error")).toBeTruthy();
    expect(screen.queryByTestId("contributors-table")).toBeNull();
    expect(screen.queryByTestId("contributors-empty")).toBeNull();
    expect(screen.queryByTestId("insights-banner")).toBeNull();
});

test("shows API notes and incomplete coverage", async () => {
    mockGetInsights.mockResolvedValue(VARIANTS.partial);
    renderPage();

    const banner = await screen.findByTestId("insights-banner");
    expect(banner.textContent).toContain("Imported window only; 2 of 5 scopes still importing");
    expect(banner.textContent).toContain("Some configured GitHub scopes are still importing.");
});

test("keeps shared and unresolved authorship explicit without percentages", async () => {
    renderPage();
    await screen.findByTestId("contributors-table");

    expect(screen.getByTestId("contributor-Sam Local").textContent).toContain("Unresolved author: Sam Local");
    const shared = screen.getByTestId("contributor-pairing-bot");
    expect(shared.textContent).toContain("pairing-bot");
    expect(shared.textContent).toContain("3");
    expect(screen.getByText(/not counted as full individual credit/i)).toBeTruthy();
    expect(document.body.textContent).not.toContain("%");
});

test("does not link an unsafe login even when it has a GitHub user id", async () => {
    renderPage();
    await screen.findByTestId("contributors-table");

    expect(screen.getByText("http://unsafe.example").closest("a")).toBeNull();
});

test("connector cards link only active or paused GitHub connectors to contributors", () => {
    const renderCard = (connector) => render(
        <MemoryRouter><ConnectorCard connector={connector} onLogs={() => {}} onDisconnect={() => {}} /></MemoryRouter>
    );
    const base = { id: "github-1", connector_type: "github", display_name: "GitHub", status: "active" };

    const active = renderCard(base);
    expect(screen.getByTestId("contributors-github-1").getAttribute("href")).toBe("/connectors/github-1/contributors");
    active.unmount();

    const disconnected = renderCard({ ...base, status: "disconnected" });
    expect(screen.queryByTestId("contributors-github-1")).toBeNull();
    disconnected.unmount();

    renderCard({ ...base, id: "other-1", connector_type: "notion" });
    expect(screen.queryByTestId("contributors-other-1")).toBeNull();
});

test("pilot mode hides the contributors route", async () => {
    process.env.REACT_APP_PILOT_MODE = "true";
    window.history.pushState({}, "", `/connectors/${CONNECTOR_ID}/contributors`);

    render(<App />);

    await waitFor(() => expect(window.location.pathname).toBe("/memories"));
    expect(screen.getByTestId("pilot-memories")).toBeTruthy();
    expect(mockGetInsights).not.toHaveBeenCalled();
});
