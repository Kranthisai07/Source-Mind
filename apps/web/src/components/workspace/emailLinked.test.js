/**
 * A second sign-in whose email is already linked to another account gets a 409
 * SM010 `email_already_linked`. A retry can never succeed, so the layout gate
 * shows an actionable message with Sign out and no Retry; generic errors keep
 * their Retry.
 */

process.env.REACT_APP_USE_MOCKS = "false";

import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

const mockSignOut = jest.fn();
jest.mock("sonner", () => ({ __esModule: true, toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock("@clerk/clerk-react", () => ({
    __esModule: true,
    useUser: () => ({ isSignedIn: true, user: { fullName: "Me", primaryEmailAddress: { emailAddress: "secret-person@example.com" } } }),
    useClerk: () => ({ signOut: (...a) => mockSignOut(...a) }),
}));

/* eslint-disable import/first */
const { resetIdentityScopedCaches } = require("../../lib/realApi");
const { setTokenGetter } = require("../../lib/authToken");
const { setCurrentUserId } = require("../../lib/currentUser");
const AppLayout = require("../layout/AppLayout").default;
/* eslint-enable import/first */

const reply = (body, status) => ({
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    headers: { get: () => null },
    text: async () => JSON.stringify(body),
});

const failWith = (status, error) => {
    global.fetch = jest.fn((url) => {
        const path = new URL(String(url), "http://localhost").pathname;
        if (path === "/v1/workspaces" || path === "/v1/team/me") return Promise.resolve(reply({ error }, status));
        return Promise.resolve(reply({}, 200));
    });
};

const renderGate = () =>
    render(
        <MemoryRouter initialEntries={["/memories"]}>
            <Routes>
                <Route element={<AppLayout />}>
                    <Route path="/memories" element={<div data-testid="page" />} />
                </Route>
            </Routes>
        </MemoryRouter>
    );

beforeEach(() => {
    setTokenGetter(null);
    setCurrentUserId("user-a");
    mockSignOut.mockReset();
});
afterEach(() => {
    resetIdentityScopedCaches();
    setCurrentUserId(null);
    delete global.fetch;
});

test("email_already_linked is explained, has no Retry, and offers Sign out", async () => {
    failWith(409, { code: "SM010", message: "conflict", details: { reason: "email_already_linked" } });
    renderGate();

    const box = await screen.findByTestId("workspaces-error");
    expect(box.getAttribute("data-error-kind")).toBe("email-linked");
    expect(box.textContent).toMatch(/already linked to another SourceMind sign-in/);
    expect(box.textContent).toMatch(/Ask the workspace owner/);
    expect(box.textContent).not.toMatch(/secret-person@example\.com/);
    expect(box.textContent).not.toMatch(/Something went wrong/);
    expect(screen.queryByTestId("workspaces-error-retry")).toBeNull();

    fireEvent.click(screen.getByTestId("workspaces-error-signout"));
    await waitFor(() => expect(mockSignOut).toHaveBeenCalledTimes(1));
});

test("a generic 409 keeps the generic message and its Retry, with no Sign out", async () => {
    failWith(409, { code: "SM031", message: "slug taken", details: null });
    renderGate();

    const box = await screen.findByTestId("workspaces-error");
    expect(box.getAttribute("data-error-kind")).toBe("unknown");
    expect(box.textContent).toMatch(/Something went wrong/);
    expect(screen.getByTestId("workspaces-error-retry")).toBeTruthy();
    expect(screen.queryByTestId("workspaces-error-signout")).toBeNull();
});
