/**
 * Search cards show real attribution in normal mode, nothing when it is
 * missing, and pilot search stays exactly as before (no attribution requested,
 * none shown).
 */

process.env.REACT_APP_USE_MOCKS = "false";

import React from "react";
import { render, screen, act, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

/* eslint-disable import/first */
const { resetIdentityScopedCaches } = require("../lib/realApi");
const { setTokenGetter } = require("../lib/authToken");
const { setCurrentUserId } = require("../lib/currentUser");
const Memories = require("./Memories").default;
/* eslint-enable import/first */

const WS = "11111111-1111-4111-8111-111111111111";
let bodies;
let results;
let failSearch;

const reply = (body, status = 200) => ({
    ok: status < 400, status, statusText: String(status),
    headers: { get: () => null }, text: async () => JSON.stringify(body),
});

const person = (name, pct, primary = false) => ({
    user: { id: `id-${name}`, display_name: name, avatar_url: null },
    contribution_weight: pct / 100, percentage: pct, is_primary: primary,
});
const item = (id, memory) => ({
    memory: { id, content: `content ${id}`, tags: [], created_at: "2026-10-01T00:00:00Z", ...memory },
    score: 0.5, rank: 1, match_type: "keyword",
});

beforeEach(() => {
    bodies = [];
    results = [];
    failSearch = false;
    setTokenGetter(null);
    setCurrentUserId("user-a");
    global.fetch = jest.fn((url, init = {}) => {
        const path = new URL(String(url), "http://localhost").pathname;
        if (path === "/v1/workspaces") return Promise.resolve(reply({ data: [{ id: WS, name: "W", slug: "w" }] }));
        if (path === "/v1/memories/search") {
            bodies.push(JSON.parse(init.body));
            return Promise.resolve(failSearch ? reply({ error: { code: "SM099" } }, 500) : reply({ results, total_found: results.length }));
        }
        return Promise.resolve(reply({}));
    });
});
afterEach(() => {
    resetIdentityScopedCaches();
    setCurrentUserId(null);
    delete global.fetch;
    delete process.env.REACT_APP_PILOT_MODE;
});

async function search() {
    render(<MemoryRouter><Memories /></MemoryRouter>);
    await act(async () => {
        fireEvent.change(screen.getByTestId("memories-search-input"), { target: { value: "release notes" } });
    });
    await waitFor(() => expect(bodies.length).toBeGreaterThan(0), { timeout: 2000 });
    await act(async () => { await new Promise((r) => setTimeout(r, 100)); });
}

test("normal mode asks for attribution and shows the real contributor", async () => {
    results = [item("m1", { attribution: [person("Frank Friend", 100, true)] })];
    await search();

    // Normal mode also asks for the source link, authorship and source state.
    expect(bodies[0]).toEqual({ query: "release notes", mode: "hybrid", limit: 24, include_attribution: true, include_source: true });
    expect(screen.getByText("Frank Friend")).toBeTruthy();
    expect(screen.getByTestId("memory-attribution-pct").textContent).toBe("100%");
    expect(document.body.textContent).not.toContain("Unattributed");
});

test("an unresolved external author is noted, not called Unattributed", async () => {
    results = [item("m2", { attribution: [], unresolved_author: { status: "unresolved", source_author: "octo-dev", source_tool: "github" } })];
    await search();

    expect(screen.getByTestId("memory-attribution-unresolved").textContent).toMatch(/octo-dev \(github\), not linked to a member/);
    expect(document.body.textContent).not.toContain("Unattributed");
});

test.each([["null", null], ["empty", []], ["missing", undefined]])("%s attribution shows nothing at all", async (_n, attribution) => {
    results = [item("m3", { attribution })];
    await search();

    expect(screen.getByText("content m3")).toBeTruthy();
    expect(screen.queryByTestId("memory-attribution-unattributed")).toBeNull();
    expect(screen.queryByTestId("memory-attribution-unresolved")).toBeNull();
    expect(document.body.textContent).not.toContain("Unattributed");
});

test("malformed attribution does not break the results", async () => {
    results = [item("m4", { attribution: "oops", unresolved_author: { status: "unresolved" } })];
    await search();

    expect(screen.getByText("content m4")).toBeTruthy();
    expect(document.body.textContent).not.toContain("Unattributed");
});

test("pilot mode sends the unchanged body and shows no attribution", async () => {
    process.env.REACT_APP_PILOT_MODE = "true";
    results = [item("m5", { attribution: [person("Hidden Person", 100, true)] })];
    await search();

    expect(bodies[0]).toEqual({ query: "release notes", mode: "hybrid", limit: 24 });
    expect(screen.queryByText("Hidden Person")).toBeNull();
    expect(screen.getByTestId("pilot-attribution-hidden")).toBeTruthy();
    expect(screen.queryByTestId("result-score")).toBeNull();
});

test("a failing search still shows the ordinary error state", async () => {
    failSearch = true;
    await search();
    await waitFor(() => expect(screen.getByTestId("error-state")).toBeTruthy());
});
