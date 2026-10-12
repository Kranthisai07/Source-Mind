/**
 * Workspace UI against the REAL api module with a mocked network.
 *
 * Mock mode is switched off before anything is imported, so the pages, the
 * layout gate, the picker and the dialogs all run through realApi.js and its
 * generation/abort machinery. Only `fetch`, Clerk and the toast are faked.
 */

process.env.REACT_APP_USE_MOCKS = "false";

import React from "react";
import { render, screen, waitFor, fireEvent, act, within } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";

const mockToastSuccess = jest.fn();
const mockToastError = jest.fn();
jest.mock("sonner", () => ({
    __esModule: true,
    toast: { success: (...a) => mockToastSuccess(...a), error: (...a) => mockToastError(...a) },
}));

jest.mock("@clerk/clerk-react", () => ({
    __esModule: true,
    useUser: () => ({
        isSignedIn: true,
        user: { fullName: "Me", primaryEmailAddress: { emailAddress: "clerk-email@example.com" } },
    }),
    useClerk: () => ({ signOut: jest.fn() }),
}));

/* eslint-disable import/first */
const { resetIdentityScopedCaches } = require("../../lib/realApi");
const { setTokenGetter } = require("../../lib/authToken");
const { setCurrentUserId } = require("../../lib/currentUser");
const AppLayout = require("../layout/AppLayout").default;
const Memories = require("../../pages/Memories").default;
const Settings = require("../../pages/Settings").default;
/* eslint-enable import/first */

const WS_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const WS_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const NEW_WS = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const ME = { id: "me-id", email: "api-email@example.com" };
const wsObj = (id, name, slug) => ({ id, name, slug, description: null });

const reply = (body, status = 200, headers = {}) => ({
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    headers: { get: (k) => headers[k] ?? null },
    text: async () => JSON.stringify(body),
});
const deferred = () => {
    let release;
    const promise = new Promise((r) => { release = r; });
    return { promise, release };
};

let S;
let calls;

function resetNetwork() {
    S = {
        workspaces: [wsObj(WS_A, "Alpha", "alpha"), wsObj(WS_B, "Beta", "beta")],
        listStatus: 200,
        me: ME,
        members: {
            [WS_A]: [{ user: { id: ME.id, display_name: "Me" }, role: "owner", joined_at: "2026-01-01T00:00:00Z" }],
            [WS_B]: [{ user: { id: ME.id, display_name: "Me" }, role: "owner", joined_at: "2026-01-01T00:00:00Z" }],
        },
        override: null,
        createReply: null,
        addReply: null,
        search: (ws) => reply({ results: [{ memory: { id: `m-${ws}`, content: ws === WS_A ? "ALPHA-ONLY memory" : "BETA-ONLY memory" } }], total_found: 1 }),
    };
    calls = [];
    global.fetch = jest.fn((url, init = {}) => {
        const u = new URL(String(url), "http://localhost");
        const method = init.method || "GET";
        const path = u.pathname;
        calls.push({ path, method, search: u.searchParams, init });
        if (S.override) {
            const o = S.override(path, method, u, init);
            if (o) return o;
        }
        let m;
        if (path === "/v1/workspaces" && method === "GET") {
            if (S.listStatus !== 200) return Promise.resolve(reply({ error: { code: "SM099" } }, S.listStatus));
            return Promise.resolve(reply({ data: S.workspaces }));
        }
        if (path === "/v1/workspaces" && method === "POST") {
            return Promise.resolve(S.createReply || reply({ data: wsObj(NEW_WS, "New", "new") }, 201));
        }
        if (path === "/v1/team/me") return Promise.resolve(reply({ data: S.me }));
        if ((m = /^\/v1\/workspaces\/([^/]+)\/members$/.exec(path))) {
            if (method === "POST") return Promise.resolve(S.addReply || reply({ data: { user: { id: "u2" }, role: "member", joined_at: "t" } }, 201));
            return Promise.resolve(reply({ data: S.members[m[1]] || [] }));
        }
        if ((m = /^\/v1\/workspaces\/([^/]+)$/.exec(path))) {
            const w = S.workspaces.find((x) => x.id === m[1]);
            return Promise.resolve(reply({ data: w || {} }));
        }
        if (path === "/v1/memories/search") return Promise.resolve(S.search(u.searchParams.get("workspace_id")));
        if (path === "/v1/memories" && method === "POST") return Promise.resolve(reply({ data: { job_id: "job-1" } }, 202));
        return Promise.resolve(reply({}));
    });
}

function Where() {
    const l = useLocation();
    return <div data-testid="where">{l.pathname}</div>;
}

function renderApp(path = "/memories") {
    return render(
        <MemoryRouter initialEntries={[path]}>
            <Where />
            <Routes>
                <Route element={<AppLayout />}>
                    <Route path="/memories" element={<Memories />} />
                    <Route path="/memories/:id" element={<div data-testid="detail-page">detail</div>} />
                    <Route path="/settings" element={<Settings />} />
                </Route>
            </Routes>
        </MemoryRouter>
    );
}

const tick = (ms = 0) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });

async function openPicker() {
    fireEvent.keyDown(await screen.findByTestId("workspace-switcher"), { key: "Enter" });
    return screen.findByTestId("workspace-menu");
}
async function switchTo(id) {
    await openPicker();
    fireEvent.click(screen.getByTestId(`workspace-option-${id}`));
}
async function search(text) {
    fireEvent.change(await screen.findByTestId("memories-search-input"), { target: { value: text } });
}

beforeAll(() => {
    global.ResizeObserver = global.ResizeObserver || class { observe() {} unobserve() {} disconnect() {} };
    Element.prototype.scrollIntoView = Element.prototype.scrollIntoView || (() => {});
    Element.prototype.hasPointerCapture = Element.prototype.hasPointerCapture || (() => false);
    Element.prototype.releasePointerCapture = Element.prototype.releasePointerCapture || (() => {});
});

beforeEach(() => {
    setTokenGetter(null);
    setCurrentUserId("user-1");
    window.localStorage.clear();
    resetIdentityScopedCaches();
    mockToastSuccess.mockReset();
    mockToastError.mockReset();
    resetNetwork();
});

afterEach(() => {
    delete global.fetch;
    setCurrentUserId(null);
    resetIdentityScopedCaches();
});

describe("zero-workspace state", () => {
    test("an empty list shows the explicit state with the signed-in email, not 'Not available'", async () => {
        S.workspaces = [];
        renderApp();
        expect(await screen.findByTestId("no-workspace")).toBeTruthy();
        expect(screen.getByText(/signed in but not in a workspace yet/i)).toBeTruthy();
        expect(screen.getByTestId("no-workspace-email").textContent).toBe("api-email@example.com");
        expect(screen.getByText(/Ask the workspace owner to add this address/i)).toBeTruthy();
        expect(screen.queryByText(/Not available/i)).toBeNull();
        // Sign-out and theme stay usable.
        expect(screen.getByTestId("no-workspace-signout")).toBeTruthy();
        expect(screen.getByTestId("theme-toggle")).toBeTruthy();
        expect(screen.getByTestId("create-workspace-form")).toBeTruthy();
    });

    test("Check again re-lists and enters the app once the user has been added", async () => {
        S.workspaces = [];
        renderApp();
        await screen.findByTestId("no-workspace");
        const listsBefore = calls.filter((c) => c.path === "/v1/workspaces" && c.method === "GET").length;

        S.workspaces = [wsObj(WS_A, "Alpha", "alpha")];
        fireEvent.click(screen.getByTestId("no-workspace-check"));

        expect(await screen.findByTestId("workspace-switcher")).toBeTruthy();
        expect(screen.queryByTestId("no-workspace")).toBeNull();
        expect(calls.filter((c) => c.path === "/v1/workspaces" && c.method === "GET").length).toBeGreaterThan(listsBefore);
    });

    test("Check again with still no workspaces keeps the state", async () => {
        S.workspaces = [];
        renderApp();
        await screen.findByTestId("no-workspace");
        fireEvent.click(screen.getByTestId("no-workspace-check"));
        await tick(20);
        expect(await screen.findByTestId("no-workspace")).toBeTruthy();
    });

    test("a failed list is an error, never treated as empty", async () => {
        S.listStatus = 500;
        renderApp();
        expect(await screen.findByTestId("workspaces-error")).toBeTruthy();
        expect(screen.queryByTestId("no-workspace")).toBeNull();
    });

    test("creating a workspace from the empty state selects it and lands on Memories", async () => {
        S.workspaces = [];
        renderApp("/settings");
        await screen.findByTestId("no-workspace");

        fireEvent.change(screen.getByTestId("create-workspace-name"), { target: { value: "Our Friends" } });
        expect(screen.getByTestId("create-workspace-slug").value).toBe("our-friends");
        S.workspaces = [wsObj(NEW_WS, "Our Friends", "our-friends")];
        fireEvent.click(screen.getByTestId("create-workspace-submit"));

        expect(await screen.findByTestId("workspace-switcher")).toBeTruthy();
        await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/memories"));
        expect(mockToastSuccess).toHaveBeenCalled();
        const post = calls.find((c) => c.path === "/v1/workspaces" && c.method === "POST");
        expect(JSON.parse(post.init.body)).toEqual({ name: "Our Friends", slug: "our-friends" });
        expect(window.localStorage.getItem("sourcemind.workspace.user-1")).toBe(NEW_WS);
    });
});

describe("workspace picker", () => {
    test("lists workspaces with name and slug and a check on the current one", async () => {
        renderApp();
        const menu = await openPicker();
        expect(within(menu).getByText("Alpha")).toBeTruthy();
        expect(within(menu).getByText("beta")).toBeTruthy();
        expect(within(screen.getByTestId(`workspace-option-${WS_A}`)).queryByLabelText("Current workspace")).toBeTruthy();
        expect(within(screen.getByTestId(`workspace-option-${WS_B}`)).queryByLabelText("Current workspace")).toBeNull();
    });

    test("switching scopes later requests to the chosen workspace and remembers it", async () => {
        renderApp();
        await screen.findByTestId("memories-empty");
        await switchTo(WS_B);
        await waitFor(() => expect(screen.getByTestId("workspace-switcher").textContent).toMatch(/Beta/));

        await search("anything");
        await screen.findByText("BETA-ONLY memory");
        const searches = calls.filter((c) => c.path === "/v1/memories/search");
        expect(searches[searches.length - 1].search.get("workspace_id")).toBe(WS_B);
        expect(window.localStorage.getItem("sourcemind.workspace.user-1")).toBe(WS_B);
    });

    test("the remembered workspace is used on the next load", async () => {
        window.localStorage.setItem("sourcemind.workspace.user-1", WS_B);
        renderApp();
        await waitFor(() => expect(screen.getByTestId("workspace-switcher").textContent).toMatch(/Beta/));
    });

    test("switching away from a detail route lands on the list", async () => {
        renderApp("/memories/some-id");
        await screen.findByTestId("detail-page");
        await switchTo(WS_B);
        await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/memories"));
    });

    test("the create dialog validates the slug client-side and sends nothing", async () => {
        renderApp();
        await openPicker();
        fireEvent.click(screen.getByTestId("workspace-create-item"));
        const dialog = await screen.findByTestId("create-workspace-dialog");
        fireEvent.change(within(dialog).getByTestId("create-workspace-name"), { target: { value: "Friends" } });
        fireEvent.change(within(dialog).getByTestId("create-workspace-slug"), { target: { value: "Bad Slug" } });
        fireEvent.click(within(dialog).getByTestId("create-workspace-submit"));
        expect(await within(dialog).findByTestId("create-workspace-slug-error")).toBeTruthy();
        expect(calls.some((c) => c.path === "/v1/workspaces" && c.method === "POST")).toBe(false);
    });

    test("slug taken (SM031) is shown on the slug field and the dialog stays open", async () => {
        S.createReply = reply({ error: { code: "SM031", message: "taken" } }, 409);
        renderApp();
        await openPicker();
        fireEvent.click(screen.getByTestId("workspace-create-item"));
        const dialog = await screen.findByTestId("create-workspace-dialog");
        fireEvent.change(within(dialog).getByTestId("create-workspace-name"), { target: { value: "Alpha" } });
        fireEvent.click(within(dialog).getByTestId("create-workspace-submit"));
        expect((await within(dialog).findByTestId("create-workspace-slug-error")).textContent).toMatch(/already taken/i);
        expect(mockToastSuccess).not.toHaveBeenCalled();
    });

    test("rate limit (429) is shown as such", async () => {
        S.createReply = reply({ error: { code: "SM015" } }, 429, { "Retry-After": "120" });
        renderApp();
        await openPicker();
        fireEvent.click(screen.getByTestId("workspace-create-item"));
        const dialog = await screen.findByTestId("create-workspace-dialog");
        fireEvent.change(within(dialog).getByTestId("create-workspace-name"), { target: { value: "Gamma" } });
        fireEvent.click(within(dialog).getByTestId("create-workspace-submit"));
        expect((await within(dialog).findByTestId("create-workspace-error")).textContent).toMatch(/120 seconds/);
    });

    test("double submit creates exactly one workspace", async () => {
        const gate = deferred();
        S.override = (path, method) => (path === "/v1/workspaces" && method === "POST" ? gate.promise : null);
        renderApp();
        await openPicker();
        fireEvent.click(screen.getByTestId("workspace-create-item"));
        const dialog = await screen.findByTestId("create-workspace-dialog");
        fireEvent.change(within(dialog).getByTestId("create-workspace-name"), { target: { value: "Gamma" } });
        const form = within(dialog).getByTestId("create-workspace-form");
        fireEvent.submit(form);
        fireEvent.submit(form);
        fireEvent.click(within(dialog).getByTestId("create-workspace-submit"));
        await tick(10);
        expect(calls.filter((c) => c.path === "/v1/workspaces" && c.method === "POST")).toHaveLength(1);

        S.workspaces = [...S.workspaces, wsObj(NEW_WS, "Gamma", "gamma")];
        gate.release(reply({ data: wsObj(NEW_WS, "Gamma", "gamma") }, 201));
        await waitFor(() => expect(mockToastSuccess).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(screen.getByTestId("workspace-switcher").textContent).toMatch(/Gamma/));
    });
});

describe("switching drops in-flight work from the previous workspace", () => {
    test("a search for A resolving after the switch is never rendered; B's data is", async () => {
        const lateA = deferred();
        S.override = (path, method, u) =>
            path === "/v1/memories/search" && u.searchParams.get("workspace_id") === WS_A ? lateA.promise : null;
        renderApp();
        await search("secret");
        await waitFor(() => expect(calls.some((c) => c.path === "/v1/memories/search")).toBe(true));

        await switchTo(WS_B);
        // A's answer arrives only now.
        await act(async () => {
            lateA.release(reply({ results: [{ memory: { id: "m-A", content: "ALPHA-ONLY memory" } }], total_found: 1 }));
            await new Promise((r) => setTimeout(r, 20));
        });
        expect(screen.queryByText("ALPHA-ONLY memory")).toBeNull();
        expect(screen.queryByTestId("error-state")).toBeNull();
        expect(mockToastError).not.toHaveBeenCalled();
        // The page was reset for B.
        expect(screen.getByTestId("memories-search-input").value).toBe("");

        await search("secret");
        expect(await screen.findByText("BETA-ONLY memory")).toBeTruthy();
        expect(screen.queryByText("ALPHA-ONLY memory")).toBeNull();
    });

    test("an ingest-job poll for A stops on switch; its late result is never rendered", async () => {
        const lateJob = deferred();
        let jobPolls = 0;
        S.override = (path) => {
            if (path.startsWith("/v1/memories/jobs/")) {
                jobPolls += 1;
                return lateJob.promise;
            }
            return null;
        };
        renderApp();
        await screen.findByTestId("ingest-open-btn");
        fireEvent.click(screen.getByTestId("ingest-open-btn"));
        fireEvent.change(await screen.findByTestId("ingest-content"), { target: { value: "alpha text" } });
        fireEvent.click(screen.getByTestId("ingest-submit"));
        await waitFor(() => expect(jobPolls).toBe(1));

        await switchTo(WS_B);
        // The sheet, job id and polling timers belong to A: all gone.
        await waitFor(() => expect(screen.queryByTestId("ingest-panel")).toBeNull());

        await act(async () => {
            lateJob.release(reply({ data: { status: "failed", error: "ALPHA-JOB-FAILURE" } }));
            await new Promise((r) => setTimeout(r, 400));
        });
        expect(screen.queryByText(/ALPHA-JOB-FAILURE/)).toBeNull();
        expect(jobPolls).toBe(1); // no further polling after the switch
    });

    test("a members fetch for A resolving late is dropped; B's members show", async () => {
        const lateMembers = deferred();
        S.members[WS_B] = [{ user: { id: "b1", display_name: "Beta Person" }, role: "member", joined_at: "2026-01-01T00:00:00Z" }];
        S.override = (path, method) =>
            method === "GET" && path === `/v1/workspaces/${WS_A}/members` ? lateMembers.promise : null;
        renderApp("/settings");
        await screen.findByTestId("tab-team");
        fireEvent.mouseDown(screen.getByTestId("tab-team"), { button: 0 });

        await switchTo(WS_B);
        await act(async () => {
            lateMembers.release(reply({ data: [{ user: { id: "a1", display_name: "Alpha Person" }, role: "member" }] }));
            await new Promise((r) => setTimeout(r, 20));
        });
        fireEvent.mouseDown(await screen.findByTestId("tab-team"), { button: 0 });
        expect(await screen.findByText("Beta Person")).toBeTruthy();
        expect(screen.queryByText("Alpha Person")).toBeNull();
    });
});

describe("pilot mode and ingestion mode", () => {
    afterEach(() => { delete process.env.REACT_APP_PILOT_MODE; });

    async function ingestBody() {
        await screen.findByTestId("ingest-open-btn");
        fireEvent.click(screen.getByTestId("ingest-open-btn"));
        fireEvent.change(await screen.findByTestId("ingest-content"), { target: { value: "some text" } });
        fireEvent.click(screen.getByTestId("ingest-submit"));
        await waitFor(() => expect(calls.some((c) => c.path === "/v1/memories" && c.method === "POST")).toBe(true));
        return JSON.parse(calls.find((c) => c.path === "/v1/memories" && c.method === "POST").init.body);
    }

    test("normal mode sends no ingestion_mode", async () => {
        renderApp();
        expect(await ingestBody()).not.toHaveProperty("ingestion_mode");
    });

    test("pilot mode still sends verbatim", async () => {
        process.env.REACT_APP_PILOT_MODE = "true";
        renderApp();
        expect((await ingestBody()).ingestion_mode).toBe("verbatim");
    });

    test("the picker works in pilot mode and the nav stays restricted", async () => {
        process.env.REACT_APP_PILOT_MODE = "true";
        renderApp();
        await openPicker();
        expect(screen.getByTestId(`workspace-option-${WS_B}`)).toBeTruthy();
        expect(screen.getByTestId("nav-memories")).toBeTruthy();
        expect(screen.queryByTestId("nav-dashboard")).toBeNull();
    });
});

describe("Settings: add member", () => {
    async function openTeamTab() {
        renderApp("/settings");
        fireEvent.mouseDown(await screen.findByTestId("tab-team"), { button: 0 });
        return screen.findByTestId("add-member-btn");
    }
    const withRole = (role) => {
        S.members[WS_A] = [{ user: { id: ME.id, display_name: "Me" }, role, joined_at: "2026-01-01T00:00:00Z" }];
    };

    test.each(["owner", "admin"])("enabled for %s", async (role) => {
        withRole(role);
        const btn = await openTeamTab();
        await waitFor(() => expect(btn.disabled).toBe(false));
    });

    test.each(["member", "viewer"])("disabled with an explanation for %s", async (role) => {
        withRole(role);
        const btn = await openTeamTab();
        await screen.findByTestId(`member-${ME.id}`);
        await waitFor(() => expect(screen.getByTestId("add-member-hint")).toBeTruthy());
        expect(btn.disabled).toBe(true);
        expect(screen.getByTestId("add-member-hint").textContent).toMatch(/owners and admins/i);
    });

    test("role options are Member (default) and Viewer, never Admin or Owner", async () => {
        const btn = await openTeamTab();
        await waitFor(() => expect(btn.disabled).toBe(false));
        fireEvent.click(btn);
        const select = await screen.findByTestId("add-member-role");
        expect(select.value).toBe("member");
        expect(Array.from(select.options).map((o) => o.value)).toEqual(["member", "viewer"]);
    });

    async function submitAdd(email = "friend@example.com") {
        const btn = await openTeamTab();
        await waitFor(() => expect(btn.disabled).toBe(false));
        fireEvent.click(btn);
        fireEvent.change(await screen.findByTestId("add-member-email"), { target: { value: email } });
        fireEvent.click(screen.getByTestId("add-member-submit"));
    }

    test.each([
        ["user_not_found", 404, "SM023", /sign in to SourceMind once/i],
        ["email_not_verified", 422, "SM010", /not verified/i],
        ["email_ambiguous", 422, "SM010", /More than one account/i],
        ["already_member", 409, "SM036", /already a member/i],
        ["handoff_in_progress", 409, "SM036", /handoff is in progress/i],
    ])("error %s is explained and nobody is reported added", async (reason, status, code, text) => {
        S.addReply = reply({ error: { code, details: { reason } } }, status);
        await submitAdd();
        expect((await screen.findByTestId("add-member-error")).textContent).toMatch(text);
        expect(mockToastSuccess).not.toHaveBeenCalled();
        expect(screen.getByTestId("add-member-dialog")).toBeTruthy();
    });

    test.each([
        [403, "SM005", /owners and admins/i],
        [429, "SM015", /Too many attempts/i],
        [503, "SM055", /Nobody was added/i],
    ])("status %s is explained", async (status, code, text) => {
        S.addReply = reply({ error: { code } }, status);
        await submitAdd();
        expect((await screen.findByTestId("add-member-error")).textContent).toMatch(text);
        expect(mockToastSuccess).not.toHaveBeenCalled();
    });

    test("a 201 toasts, refreshes the member list and clears the form", async () => {
        await submitAdd("friend@example.com");
        await waitFor(() => expect(mockToastSuccess).toHaveBeenCalledTimes(1));
        const post = calls.find((c) => c.path === `/v1/workspaces/${WS_A}/members` && c.method === "POST");
        expect(JSON.parse(post.init.body)).toEqual({ email: "friend@example.com", role: "member" });
        // List fetched again after the add.
        const gets = calls.filter((c) => c.path === `/v1/workspaces/${WS_A}/members` && c.method === "GET");
        await waitFor(() => expect(calls.filter((c) => c.path === `/v1/workspaces/${WS_A}/members` && c.method === "GET").length).toBeGreaterThan(gets.length - 1));
        await waitFor(() => expect(screen.queryByTestId("add-member-dialog")).toBeNull());
        fireEvent.click(screen.getByTestId("add-member-btn"));
        expect((await screen.findByTestId("add-member-email")).value).toBe("");
    });

    test("double submit sends one request", async () => {
        const gate = deferred();
        S.override = (path, method) => (method === "POST" && path.endsWith("/members") ? gate.promise : null);
        await submitAdd();
        fireEvent.click(screen.getByTestId("add-member-submit"));
        await tick(10);
        expect(calls.filter((c) => c.method === "POST" && c.path.endsWith("/members"))).toHaveLength(1);
        gate.release(reply({ data: {} }, 201));
        await waitFor(() => expect(mockToastSuccess).toHaveBeenCalledTimes(1));
    });
});


describe("review follow-ups: StrictMode and dismissed creation", () => {
    function renderStrict(path = "/memories") {
        return render(
            <React.StrictMode>
                <MemoryRouter initialEntries={[path]}>
                    <Where />
                    <Routes>
                        <Route element={<AppLayout />}>
                            <Route path="/memories" element={<Memories />} />
                            <Route path="/settings" element={<Settings />} />
                        </Route>
                    </Routes>
                </MemoryRouter>
            </React.StrictMode>
        );
    }

    test("under StrictMode a rejected create shows its error and re-enables the form", async () => {
        S.workspaces = [];
        S.createReply = reply({ error: { code: "SM031", message: "taken" } }, 409);
        renderStrict();
        await screen.findByTestId("no-workspace");
        fireEvent.change(screen.getByTestId("create-workspace-name"), { target: { value: "Taken" } });
        fireEvent.click(screen.getByTestId("create-workspace-submit"));
        expect((await screen.findByTestId("create-workspace-slug-error")).textContent).toMatch(/already taken/i);
        await waitFor(() => expect(screen.getByTestId("create-workspace-submit").disabled).toBe(false));
        expect(screen.getByTestId("create-workspace-submit").textContent).toBe("Create workspace");
    });

    test("under StrictMode a rejected add shows its error and re-enables the dialog", async () => {
        S.addReply = reply({ error: { code: "SM023", details: { reason: "user_not_found" } } }, 404);
        renderStrict("/settings");
        fireEvent.mouseDown(await screen.findByTestId("tab-team"), { button: 0 });
        const btn = await screen.findByTestId("add-member-btn");
        await waitFor(() => expect(btn.disabled).toBe(false));
        fireEvent.click(btn);
        fireEvent.change(await screen.findByTestId("add-member-email"), { target: { value: "x@example.com" } });
        fireEvent.click(screen.getByTestId("add-member-submit"));
        expect((await screen.findByTestId("add-member-error")).textContent).toMatch(/sign in to SourceMind once/i);
        await waitFor(() => expect(screen.getByTestId("add-member-submit").disabled).toBe(false));
    });

    test("add member against a server without the route explains it", async () => {
        S.addReply = reply({ detail: "Method Not Allowed" }, 405);
        renderStrict("/settings");
        fireEvent.mouseDown(await screen.findByTestId("tab-team"), { button: 0 });
        const btn = await screen.findByTestId("add-member-btn");
        await waitFor(() => expect(btn.disabled).toBe(false));
        fireEvent.click(btn);
        fireEvent.change(await screen.findByTestId("add-member-email"), { target: { value: "x@example.com" } });
        fireEvent.click(screen.getByTestId("add-member-submit"));
        expect((await screen.findByTestId("add-member-error")).textContent).toMatch(/does not support adding members yet/);
        expect(mockToastSuccess).not.toHaveBeenCalled();
    });

    test("dismissing the dialog while creating does not switch workspace or navigate", async () => {
        const gate = deferred();
        S.override = (path, method) => (path === "/v1/workspaces" && method === "POST" ? gate.promise : null);
        renderApp("/settings");
        await openPicker();
        fireEvent.click(screen.getByTestId("workspace-create-item"));
        const dialog = await screen.findByTestId("create-workspace-dialog");
        fireEvent.change(within(dialog).getByTestId("create-workspace-name"), { target: { value: "Gamma" } });
        fireEvent.click(within(dialog).getByTestId("create-workspace-submit"));
        await waitFor(() => expect(calls.some((c) => c.path === "/v1/workspaces" && c.method === "POST")).toBe(true));

        fireEvent.keyDown(dialog, { key: "Escape" });
        await waitFor(() => expect(screen.queryByTestId("create-workspace-dialog")).toBeNull());

        gate.release(reply({ data: wsObj(NEW_WS, "Gamma", "gamma") }, 201));
        await waitFor(() => expect(mockToastSuccess).toHaveBeenCalledTimes(1));
        expect(screen.getByTestId("where").textContent).toBe("/settings");
        expect(screen.getByTestId("workspace-switcher").textContent).toMatch(/Alpha/);
        // It is listed, just not selected.
        await openPicker();
        expect(screen.getByTestId(`workspace-option-${NEW_WS}`)).toBeTruthy();
    });
});