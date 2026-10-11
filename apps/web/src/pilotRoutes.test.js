import React from "react";
import { render, screen, waitFor } from "@testing-library/react";

const mockGetAnalyticsOverview = jest.fn();
const mockListConflicts = jest.fn();
const mockListContributors = jest.fn();
const mockListHandoffs = jest.fn();
const mockListConnectors = jest.fn();

jest.mock("@clerk/clerk-react", () => ({
    __esModule: true,
    useAuth: () => ({
        isLoaded: true,
        isSignedIn: true,
        userId: "pilot-user",
        getToken: async () => null,
    }),
    useUser: () => ({
        isSignedIn: true,
        user: {
            fullName: "Pilot User",
            primaryEmailAddress: { emailAddress: "pilot@example.invalid" },
        },
    }),
    useClerk: () => ({ signOut: jest.fn() }),
    SignIn: () => null,
    SignUp: () => null,
}));

jest.mock("react-markdown", () => ({
    __esModule: true,
    default: ({ children }) => children ?? null,
}));
jest.mock("remark-gfm", () => ({ __esModule: true, default: () => {} }));

jest.mock("@/components/layout/AppLayout", () => {
    const ReactModule = require("react");
    const { Outlet } = require("react-router-dom");
    return {
        __esModule: true,
        default: () => ReactModule.createElement("main", null, ReactModule.createElement(Outlet)),
    };
});

jest.mock("@/lib/api", () => ({
    __esModule: true,
    useMocks: true,
    resetApiCaches: jest.fn(),
    resolveCurrentWorkspace: jest.fn(async () => null),
    default: {
        getWorkspace: jest.fn(async () => ({ name: "Pilot", slug: "pilot" })),
        searchMemories: jest.fn(async () => ({ results: [], total_found: 0, latency_ms: 1 })),
        getAnalyticsOverview: (...args) => mockGetAnalyticsOverview(...args),
        listConflicts: (...args) => mockListConflicts(...args),
        listContributors: (...args) => mockListContributors(...args),
        listHandoffs: (...args) => mockListHandoffs(...args),
        listConnectors: (...args) => mockListConnectors(...args),
    },
}));

import App from "./App";

const goTo = (path) => window.history.pushState({}, "", path);
const restrictedRoutes = [
    "/dashboard",
    "/analytics",
    "/handoff",
    "/conflicts",
    "/conflicts/11111111-1111-4111-8111-111111111111",
    "/connectors",
];

beforeEach(() => {
    process.env.REACT_APP_PILOT_MODE = "true";
    jest.clearAllMocks();
});

afterEach(() => {
    delete process.env.REACT_APP_PILOT_MODE;
    goTo("/");
});

test.each(restrictedRoutes)("pilot mode redirects direct navigation from %s", async (path) => {
    goTo(path);
    render(<App />);

    await waitFor(() => expect(window.location.pathname).toMatch(/\/memories$/));
    expect(await screen.findByTestId("memories-empty")).toBeTruthy();
    expect(mockGetAnalyticsOverview).not.toHaveBeenCalled();
    expect(mockListConflicts).not.toHaveBeenCalled();
    expect(mockListContributors).not.toHaveBeenCalled();
    expect(mockListHandoffs).not.toHaveBeenCalled();
    expect(mockListConnectors).not.toHaveBeenCalled();
});
