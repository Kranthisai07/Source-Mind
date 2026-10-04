import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const mockGetOverview = jest.fn();
const mockGetGaps = jest.fn();
const mockListContributors = jest.fn();

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
    useMocks: false,
    default: {
        getAnalyticsOverview: (...args) => mockGetOverview(...args),
        getKnowledgeGaps: (...args) => mockGetGaps(...args),
        listContributors: (...args) => mockListContributors(...args),
    },
}));

import Analytics, { TreemapNode } from "./Analytics";
import Dashboard from "./Dashboard";
import AttributionBar from "../components/widgets/AttributionBar";
import HealthGauge from "../components/widgets/HealthGauge";

beforeEach(() => {
    jest.clearAllMocks();
    document.documentElement.classList.remove("dark");
    mockGetOverview.mockResolvedValue({
        total_memories: 12,
        total_contributors: 3,
        memories_created_last_30_days: 4,
        open_conflicts: 0,
        knowledge_health_score: 82,
        recent_activity: [],
        top_contributors: [],
        health_breakdown: {
            coverage: 80,
            freshness: 81,
            conflict_ratio: 83,
            attribution_coverage: 84,
        },
    });
    mockGetGaps.mockRejectedValue(Object.assign(new Error("Gap service unavailable"), { status: 503 }));
    mockListContributors.mockResolvedValue({
        contributors: [{
            id: "synthetic-user",
            login: "synthetic-user",
            count: 4,
            score: 0.88,
            last_active: "today",
            top_category: "architecture",
            avatarColor: "#4F7EFF",
        }],
    });
});

test("keeps successful analytics panels when another panel fails", async () => {
    render(<Analytics />);

    expect((await screen.findByTestId("health-gauge-score")).textContent).toContain("82");
    expect(screen.queryByTestId("analytics-error")).toBeNull();

    fireEvent.mouseDown(screen.getByTestId("tab-contribution"), { button: 0, ctrlKey: false });
    expect(document.body.textContent).toContain("@synthetic-user");

    fireEvent.mouseDown(screen.getByTestId("tab-gaps"), { button: 0, ctrlKey: false });
    expect((await screen.findByTestId("analytics-gaps-error")).textContent).toContain("Gap service unavailable");
    expect(document.body.textContent).not.toContain("6 pts vs last month");
});

test("dashboard labels current metrics without unsupported trend claims", async () => {
    mockGetGaps.mockResolvedValue({ gaps: [] });

    render(
        <MemoryRouter>
            <Dashboard />
        </MemoryRouter>
    );

    expect((await screen.findByTestId("metric-contributors")).textContent).toContain("Current workspace total");
    expect(screen.getByTestId("metric-new-this-month").textContent).toContain("Rolling 30-day count");
    expect(document.body.textContent).not.toContain("All active this week");
    expect(document.body.textContent).not.toContain("18% vs. last 30d");
});

test.each(["light", "dark"])("uses readable theme tokens for populated charts in %s mode", (theme) => {
    document.documentElement.classList.toggle("dark", theme === "dark");

    const { getByTestId } = render(
        <>
            <HealthGauge score={82} />
            <svg>
                <TreemapNode x={0} y={0} width={120} height={60} name="synthetic-user" fill="#4F7EFF" />
            </svg>
        </>
    );

    expect(getByTestId("health-gauge-score").style.fill).toBe("var(--sm-text)");
    expect(getByTestId("treemap-label").getAttribute("fill")).toBe("var(--sm-text)");
    expect(getByTestId("treemap-label").textContent).toContain("@synthetic-user");
});

test("accepts explicit null attribution as an empty contribution set", () => {
    const { getByTestId } = render(<AttributionBar attribution={null} />);
    expect(getByTestId("attribution-bar").querySelectorAll("[title]")).toHaveLength(0);
});
