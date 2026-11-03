/** Settings offers role controls from the signed-in user's role, whatever its letter case. */

import React from "react";
import { MemoryRouter } from "react-router-dom";
import { fireEvent, render, screen } from "@testing-library/react";

jest.mock("../components/settings/McpKeysSection", () => () => null);
jest.mock("../components/team/PendingInvitations", () => () => null);
jest.mock("../components/team/ActivityTab", () => () => null);
jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        getWorkspace: jest.fn(),
        listTeamMembers: jest.fn(),
        getCurrentUser: jest.fn(),
    },
}));

import api from "../lib/api";
import Settings from "./Settings";

const members = (myRole) => ({
    members: [
        { user: { id: "me", display_name: "Me" }, role: myRole },
        { user: { id: "ann", display_name: "Ann" }, role: "member" },
    ],
});

async function open(myRole) {
    api.getWorkspace.mockResolvedValue({ id: "w1", name: "W", slug: "w" });
    api.getCurrentUser.mockResolvedValue({ id: "me" });
    api.listTeamMembers.mockResolvedValue(members(myRole));
    render(<MemoryRouter><Settings /></MemoryRouter>);
    fireEvent.mouseDown(await screen.findByTestId("tab-team"));
    fireEvent.click(screen.getByTestId("tab-team"));
}

test.each(["owner", "Owner", "OWNER"])("founder role %s gets a role menu on another member", async (role) => {
    await open(role);
    expect(await screen.findByTestId("role-select-ann")).toBeTruthy();
});

test("a teammate gets no role menu", async () => {
    await open("member");
    await screen.findByTestId("member-ann");
    expect(screen.queryByTestId("role-select-ann")).toBeNull();
});

test("the Activity tab is present but disabled for a teammate, enabled for a founder", async () => {
    await open("member");
    expect(screen.getByTestId("tab-activity").disabled).toBe(true);
});

test("the Activity tab is enabled for a founder", async () => {
    await open("owner");
    await screen.findByTestId("role-select-ann");
    expect(screen.getByTestId("tab-activity").disabled).toBe(false);
});
