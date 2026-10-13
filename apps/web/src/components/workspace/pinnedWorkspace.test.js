/**
 * A build that sets REACT_APP_DEFAULT_WORKSPACE_ID is pinned to that workspace.
 * Offering a picker would show one workspace as selected while every request
 * still went to the configured one, so the picker and creation entry points
 * are absent and selectWorkspace cannot desync anything.
 */

process.env.REACT_APP_USE_MOCKS = "false";
const PINNED = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
process.env.REACT_APP_DEFAULT_WORKSPACE_ID = PINNED;

import React from "react";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

jest.mock("sonner", () => ({ __esModule: true, toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock("@clerk/clerk-react", () => ({
    __esModule: true,
    useUser: () => ({ isSignedIn: true, user: { fullName: "Me", primaryEmailAddress: { emailAddress: "me@example.com" } } }),
    useClerk: () => ({ signOut: jest.fn() }),
}));

/* eslint-disable import/first */
const realApiModule = require("../../lib/realApi");
const { setTokenGetter } = require("../../lib/authToken");
const AppLayout = require("../layout/AppLayout").default;
const Memories = require("../../pages/Memories").default;
/* eslint-enable import/first */

const reply = (body) => ({
    ok: true, status: 200, statusText: "OK", headers: { get: () => null }, text: async () => JSON.stringify(body),
});
const OTHER = { id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", name: "Other", slug: "other" };

let urls;
beforeEach(() => {
    setTokenGetter(null);
    urls = [];
    global.fetch = jest.fn((u) => {
        const url = String(u);
        urls.push(url);
        if (/\/v1\/workspaces$/.test(url.split("?")[0])) {
            return Promise.resolve(reply({ data: [{ id: PINNED, name: "Pinned", slug: "pinned" }, OTHER] }));
        }
        return Promise.resolve(reply({ results: [], total_found: 0 }));
    });
});
afterEach(() => { delete global.fetch; });

test("selectWorkspace is a no-op: no scope change, requests stay on the configured workspace", async () => {
    const before = realApiModule.workspaceGeneration();
    realApiModule.selectWorkspace(OTHER.id);
    expect(realApiModule.workspaceGeneration()).toBe(before);
    expect(realApiModule.isWorkspacePinned()).toBe(true);
    await realApiModule.realApi.searchMemories({ query: "q" });
    expect(urls.some((u) => u.includes(`workspace_id=${PINNED}`))).toBe(true);
    expect(urls.some((u) => u.includes(OTHER.id))).toBe(false);
});

test("the sidebar shows a fixed workspace label with no picker or create entry", async () => {
    render(
        <MemoryRouter initialEntries={["/memories"]}>
            <Routes>
                <Route element={<AppLayout />}>
                    <Route path="/memories" element={<Memories />} />
                </Route>
            </Routes>
        </MemoryRouter>
    );
    const sw = await screen.findByTestId("workspace-switcher");
    expect(sw.getAttribute("data-pinned")).toBe("true");
    expect(sw.textContent).toMatch(/Pinned/);
    await act(async () => { fireEvent.keyDown(sw, { key: "Enter" }); });
    expect(screen.queryByTestId("workspace-menu")).toBeNull();
    expect(screen.queryByTestId("workspace-create-item")).toBeNull();
});
