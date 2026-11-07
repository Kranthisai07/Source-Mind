import React from "react";
import { MemoryRouter } from "react-router-dom";
import { render, screen } from "@testing-library/react";
import TopBar from "./TopBar";
import ActivityTab from "../team/ActivityTab";
import PendingInvitations from "../team/PendingInvitations";
import api from "../../lib/api";

jest.mock("sonner", () => ({ toast: { success: jest.fn() } }));
jest.mock("../../lib/api", () => ({
    __esModule: true,
    default: { listWorkspaceAudit: jest.fn(), exportWorkspace: jest.fn(), listInvitations: jest.fn(), revokeInvitation: jest.fn() },
}));

// jsdom does not lay anything out, so these pin the class contract that fixed three phone-width bugs
// seen on the live site at 390px (D-080); the layout itself was checked in a browser.
const has = (el, ...classes) => classes.forEach((c) => expect(el.className.split(/\s+/)).toContain(c));

test("the top bar lets its actions wrap below the title instead of squeezing the subtitle", () => {
    render(
        <MemoryRouter>
            <TopBar title="Dashboard" subtitle="5 memories · 1 contributors · health 25/100" actions={<button>Browse memories</button>} />
        </MemoryRouter>
    );
    has(screen.getByTestId("topbar"), "flex-wrap");
    has(screen.getByText("Dashboard").parentElement, "flex-1", "min-w-0");
});

test("a pending invitation keeps its email visible and the button inside the card on a phone", async () => {
    api.listInvitations.mockResolvedValue({
        items: [{ id: "i1", email: "a-rather-long-address@example.com", role: "member", expires_at: "2026-11-14T00:00:00Z" }],
    });
    render(<PendingInvitations workspaceId="w1" />);
    const email = await screen.findByTestId("invitation-email-i1");
    has(email, "basis-full", "min-w-0", "truncate");
    const row = screen.getByTestId("invitation-i1");
    has(row, "flex", "flex-wrap");
    expect(row.className).not.toMatch(/grid-cols/);
    has(screen.getByTestId("revoke-i1"), "ml-auto", "shrink-0");
});

test("an activity row wraps a long email and puts the date on its own line on a phone", async () => {
    api.listWorkspaceAudit.mockResolvedValue({
        items: [{ id: 7, event: "invitation_created", actor_name: "Ana", target_email: "release-check-with-a-long-address@example.com", role: "member", at: "2026-11-07T10:00:00Z" }],
    });
    render(<ActivityTab workspaceId="w1" />);
    const row = await screen.findByTestId("activity-7");
    has(row, "flex-wrap");
    const [, text, date] = Array.from(row.children);
    has(text, "flex-1", "min-w-0");
    expect(text.className).toContain("[overflow-wrap:anywhere]");
    has(date, "basis-full", "shrink-0");
    has(screen.getByTestId("activity-tab").firstElementChild, "flex-wrap");
});
