/** The invitation landing page: nothing is consumed until the button is pressed. */

import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { fireEvent, render, screen } from "@testing-library/react";

let mockAuth = { isLoaded: true, isSignedIn: true };
jest.mock("@clerk/clerk-react", () => ({ useAuth: () => mockAuth }));
jest.mock("../lib/api", () => ({
    __esModule: true,
    default: { acceptInvitation: jest.fn() },
    useMocks: false,
}));
jest.mock("../lib/realApi", () => ({ selectWorkspace: jest.fn() }));

import api from "../lib/api";
import { selectWorkspace } from "../lib/realApi";
import InviteAccept from "./InviteAccept";

const TOKEN = `smi_${"D".repeat(43)}`;
const WS = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

const renderAt = (token) =>
    render(
        <MemoryRouter initialEntries={[`/invite/${token}`]}>
            <Routes>
                <Route path="/invite/:token" element={<InviteAccept />} />
                <Route path="/memories" element={<div data-testid="memories" />} />
            </Routes>
        </MemoryRouter>
    );

beforeEach(() => {
    mockAuth = { isLoaded: true, isSignedIn: true };
    api.acceptInvitation.mockReset();
    selectWorkspace.mockReset();
});

test("opening the page does not accept anything", () => {
    renderAt(TOKEN);
    expect(screen.getByTestId("invite-accept")).toBeTruthy();
    expect(api.acceptInvitation).not.toHaveBeenCalled();
});

test("accepting joins, selects that workspace, and opens it", async () => {
    api.acceptInvitation.mockResolvedValue({ workspace_id: WS, role: "member" });
    renderAt(TOKEN);
    fireEvent.click(screen.getByTestId("invite-accept"));
    expect((await screen.findByTestId("invite-accepted")).textContent).toMatch(/joined/);
    expect(api.acceptInvitation).toHaveBeenCalledWith(TOKEN);
    expect(selectWorkspace).toHaveBeenCalledWith(WS);
    fireEvent.click(screen.getByTestId("invite-open"));
    expect(await screen.findByTestId("memories")).toBeTruthy();
});

test("every refusal reads the same and offers no hint about the cause", async () => {
    api.acceptInvitation.mockRejectedValue({ status: 404, body: { error: { code: "SM056" } } });
    renderAt(TOKEN);
    fireEvent.click(screen.getByTestId("invite-accept"));
    expect((await screen.findByTestId("invite-accept-error")).textContent).toMatch(/can't be used/);
    expect(selectWorkspace).not.toHaveBeenCalled();
    // The button is available again so a corrected sign-in can retry.
    expect(screen.getByTestId("invite-accept").disabled).toBe(false);
});

test("a double click accepts once", async () => {
    let release;
    api.acceptInvitation.mockImplementation(() => new Promise((resolve) => { release = () => resolve({ workspace_id: WS, role: "viewer" }); }));
    renderAt(TOKEN);
    fireEvent.click(screen.getByTestId("invite-accept"));
    fireEvent.click(screen.getByTestId("invite-accept"));
    expect(api.acceptInvitation).toHaveBeenCalledTimes(1);
    release();
    await screen.findByTestId("invite-accepted");
});

test("signed out, it offers sign-in that returns here, and still accepts nothing", () => {
    mockAuth = { isLoaded: true, isSignedIn: false };
    renderAt(TOKEN);
    const signIn = screen.getByText("Sign in").closest("a");
    expect(signIn.getAttribute("href")).toBe(`/sign-in?next=${encodeURIComponent(`/invite/${TOKEN}`)}`);
    expect(screen.queryByTestId("invite-accept")).toBeNull();
    expect(api.acceptInvitation).not.toHaveBeenCalled();
});

test("a malformed link is rejected before anything is sent", () => {
    renderAt("smi_short");
    expect(screen.queryByTestId("invite-accept")).toBeNull();
    expect(screen.getByText(/isn't a valid invitation/)).toBeTruthy();
});
