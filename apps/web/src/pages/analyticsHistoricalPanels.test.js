import React from "react";
import { render, screen } from "@testing-library/react";

const mockGetOverview = jest.fn();
const mockGetGaps = jest.fn();
const mockListContributors = jest.fn();
const mockGetMemoriesOverTime = jest.fn();
const mockGetSearchActivity = jest.fn();

jest.mock("recharts", () => {
    const React = require("react");
    const Container = ({ children }) => React.createElement("div", null, children);
    const Empty = () => null;
    return {
        Area: Empty,
        AreaChart: Container,
        ResponsiveContainer: Container,
        Tooltip: Empty,
        XAxis: Empty,
        YAxis: Empty,
        LineChart: Container,
        Line: Empty,
        Treemap: Empty,
    };
});

jest.mock("../lib/api", () => ({
    __esModule: true,
    useMocks: true,
    default: {
        getAnalyticsOverview: (...args) => mockGetOverview(...args),
        getKnowledgeGaps: (...args) => mockGetGaps(...args),
        listContributors: (...args) => mockListContributors(...args),
        getMemoriesOverTime: (...args) => mockGetMemoriesOverTime(...args),
        getSearchActivity: (...args) => mockGetSearchActivity(...args),
    },
}));

import Analytics from "./Analytics";

beforeEach(() => {
    jest.clearAllMocks();
    mockGetOverview.mockResolvedValue({
        knowledge_health_score: 82,
        health_breakdown: {},
    });
    mockGetGaps.mockResolvedValue({ gaps: [] });
    mockListContributors.mockResolvedValue({ contributors: [] });
    mockGetMemoriesOverTime.mockResolvedValue({ series: [{ day: "Oct 4", count: 3 }] });
    mockGetSearchActivity.mockResolvedValue({ series: [{ day: "Oct 4", searches: 7 }] });
});

test("renders memory history while search activity is still loading", async () => {
    mockGetSearchActivity.mockImplementation(() => new Promise(() => {}));

    render(<Analytics />);

    expect(await screen.findByTestId("analytics-memory-series-chart")).toBeTruthy();
    expect(screen.getByTestId("analytics-search-series-loading")).toBeTruthy();
});

test("renders search activity while memory history is still loading", async () => {
    mockGetMemoriesOverTime.mockImplementation(() => new Promise(() => {}));

    render(<Analytics />);

    expect(await screen.findByTestId("analytics-search-series-chart")).toBeTruthy();
    expect(screen.getByTestId("analytics-memory-series-loading")).toBeTruthy();
});

test("keeps memory history visible when search activity fails", async () => {
    mockGetSearchActivity.mockRejectedValue(Object.assign(new Error("Search unavailable"), { status: 503 }));

    render(<Analytics />);

    expect(await screen.findByTestId("analytics-memory-series-chart")).toBeTruthy();
    expect(await screen.findByTestId("analytics-search-series-error")).toBeTruthy();
});
