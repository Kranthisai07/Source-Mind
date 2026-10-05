import React from "react";
import { render, screen } from "@testing-library/react";

const mockListContributors = jest.fn();
const mockListHandoffs = jest.fn();

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        listContributors: (...args) => mockListContributors(...args),
        listHandoffs: (...args) => mockListHandoffs(...args),
        classifyHandoff: jest.fn(),
    },
}));

import Handoff from "./Handoff";

beforeEach(() => {
    jest.clearAllMocks();
    mockListContributors.mockResolvedValue({ contributors: [] });
    mockListHandoffs.mockResolvedValue({
        handoffs: [{
            id: "handoff-1",
            departing_user_id: "00000000-0000-4000-8000-000000000001",
            departing_user_name: "Departing Member",
            receiving_user_id: "00000000-0000-4000-8000-000000000002",
            receiving_user_name: "Receiving Member",
            tier_1_count: 1,
            tier_2_count: 0,
            tier_3_count: 0,
            assigned_count: 1,
            status: "in_progress",
            created_at: "2026-10-04T12:00:00Z",
        }],
    });
});

test("qualifies successor suggestions when no eligible contributor exists", async () => {
    render(<Handoff />);

    expect(await screen.findByText(/when an eligible related contributor is available/i)).toBeTruthy();
    expect(document.body.textContent).not.toContain("for every CRITICAL item");
});

test("does not expose handoff user UUIDs as contributor handles", async () => {
    render(<Handoff />);

    const card = await screen.findByTestId("handoff-handoff-1");
    const avatarTitles = Array.from(card.querySelectorAll('[data-testid="contributor-avatar"]'))
        .map((avatar) => avatar.getAttribute("title"));

    expect(avatarTitles).toEqual(["Departing Member", "Receiving Member"]);
    expect(avatarTitles.join(" ")).not.toContain("@");
    expect(avatarTitles.join(" ")).not.toContain("00000000-0000-4000-8000");
});
