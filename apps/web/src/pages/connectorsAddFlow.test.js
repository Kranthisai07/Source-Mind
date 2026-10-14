/**
 * The real GitHub connect flow: authorize -> callback -> pick -> consent -> create.
 * There is no fake-success path: the panel only closes after createConnector
 * resolves, and every failure is shown. The API is mocked; no GitHub call.
 */

import React from "react";
import { render, screen, act, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";

import CALLBACK from "./__fixtures__/githubCallback.real.json";

const mockList = jest.fn();
const mockAuthorize = jest.fn();
const mockCallback = jest.fn();
const mockCreate = jest.fn();
const mockRedirect = jest.fn();

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        listConnectors: (...a) => mockList(...a),
        getConnectorStatus: jest.fn(async () => { throw { status: 503, body: null }; }),
        authorizeGithub: (...a) => mockAuthorize(...a),
        completeGithubCallback: (...a) => mockCallback(...a),
        createConnector: (...a) => mockCreate(...a),
        getSyncLogs: jest.fn(async () => ({ items: [], total: 0 })),
    },
}));

jest.mock("../components/connectors/connectorText", () => ({
    ...jest.requireActual("../components/connectors/connectorText"),
    redirectTo: (...a) => mockRedirect(...a),
}));

import Connectors from "./Connectors";
import GithubCallbackRoute from "../components/connectors/GithubCallbackRoute";
import { takeGithubCallback } from "../components/connectors/connectorText";

const settle = async (ms = 80) => {
    await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
};

function Probe() {
    const location = useLocation();
    return <div data-testid="probe">{location.pathname + location.search}</div>;
}

function renderAt(entry, { strict = false } = {}) {
    const tree = (
        <MemoryRouter initialEntries={[entry]}>
            <Probe />
            <Routes>
                <Route path="/connectors" element={<Connectors />} />
                <Route path="/connectors/github/callback" element={<GithubCallbackRoute />} />
            </Routes>
        </MemoryRouter>
    );
    return render(strict ? <React.StrictMode>{tree}</React.StrictMode> : tree);
}

const CALLBACK_URL = "/connectors/github/callback?code=the-code&state=the-state&installation_id=77";

beforeEach(() => {
    takeGithubCallback(); // never leak a stash between tests
    mockList.mockReset().mockResolvedValue({ items: [], connectors: [], total: 0 });
    mockAuthorize.mockReset().mockResolvedValue({
        authorize_url: "https://github.com/login/oauth/authorize?client_id=x&state=s",
        state: "s",
        expires_in_seconds: 600,
    });
    mockCallback.mockReset().mockResolvedValue(CALLBACK);
    mockCreate.mockReset().mockResolvedValue({ id: "new-1", display_name: "GitHub: acme-admin" });
    mockRedirect.mockReset();
});

describe("starting the flow", () => {
    test("Add connector -> GitHub -> Continue sends the browser to GitHub's authorize URL", async () => {
        renderAt("/connectors");
        await settle();

        fireEvent.click(screen.getByTestId("add-connector-btn"));
        fireEvent.click(await screen.findByTestId("type-github"));
        await act(async () => { fireEvent.click(screen.getByTestId("github-authorize-btn")); });
        await settle();

        expect(mockAuthorize).toHaveBeenCalledTimes(1);
        expect(mockRedirect).toHaveBeenCalledWith("https://github.com/login/oauth/authorize?client_id=x&state=s");
    });

    test("an authorize URL that is not GitHub's is not followed", async () => {
        mockAuthorize.mockResolvedValue({ authorize_url: "https://evil.example/steal", state: "s", expires_in_seconds: 1 });
        renderAt("/connectors");
        await settle();

        fireEvent.click(screen.getByTestId("add-connector-btn"));
        fireEvent.click(await screen.findByTestId("type-github"));
        await act(async () => { fireEvent.click(screen.getByTestId("github-authorize-btn")); });
        await settle();

        expect(mockRedirect).not.toHaveBeenCalled();
        expect(screen.getByTestId("add-flow-error").textContent).toMatch(/unexpected address/i);
    });

    test("Discord, Slack and Notion are visibly coming soon and inert; there is no drop zone", async () => {
        renderAt("/connectors");
        await settle();
        fireEvent.click(screen.getByTestId("add-connector-btn"));

        for (const id of ["type-discord", "type-slack", "type-notion"]) {
            const card = await screen.findByTestId(id);
            expect(card.disabled).toBe(true);
            expect(card.textContent).toMatch(/Coming soon/);
        }
        expect(document.body.textContent).not.toMatch(/drop/i);
        expect(screen.queryByTestId("connector-connect-btn")).toBeNull();
    });
});

describe("returning from GitHub", () => {
    test("redeems code+state once, then removes them from the URL", async () => {
        const logs = ["log", "info", "warn", "error", "debug"].map((m) => jest.spyOn(console, m).mockImplementation(() => {}));
        renderAt(CALLBACK_URL);
        await settle();

        expect(mockCallback).toHaveBeenCalledTimes(1);
        expect(mockCallback).toHaveBeenCalledWith({ code: "the-code", state: "the-state", installation_id: 77 });
        expect(screen.getByTestId("probe").textContent).toBe("/connectors");
        // The single-use code never reaches the console.
        const logged = JSON.stringify(logs.flatMap((spy) => spy.mock.calls));
        expect(logged).not.toContain("the-code");
        expect(logged).not.toContain("the-state");
        logs.forEach((spy) => spy.mockRestore());
        expect(screen.getByTestId("repo-picker")).toBeTruthy();
    });

    test("redeems only once under React Strict Mode", async () => {
        renderAt(CALLBACK_URL, { strict: true });
        await settle();
        expect(mockCallback).toHaveBeenCalledTimes(1);
        expect(screen.getByTestId("repo-picker")).toBeTruthy();
    });

    test("a callback with a bad state is refused and shows no picker", async () => {
        mockCallback.mockRejectedValue({ status: 403, body: { error: { code: "SM002", details: { reason: "connect_state_invalid" } } } });
        renderAt(CALLBACK_URL);
        await settle();

        expect(screen.getByTestId("add-flow-error").textContent).toMatch(/expired|already used/i);
        expect(screen.queryByTestId("repo-picker")).toBeNull();
        expect(mockCreate).not.toHaveBeenCalled();
    });

    test("a denial at GitHub is reported as such and nothing is redeemed", async () => {
        renderAt("/connectors/github/callback?error=access_denied&state=s");
        await settle();

        expect(mockCallback).not.toHaveBeenCalled();
        expect(screen.getByTestId("add-flow-error").textContent).toMatch(/cancelled or denied/i);
    });

    test("different failures read differently", async () => {
        const text = async (reason, status = 422) => {
            mockCallback.mockReset().mockRejectedValue({ status, body: { error: { details: { reason } } } });
            takeGithubCallback();
            const view = renderAt(CALLBACK_URL);
            await settle();
            const message = screen.getByTestId("add-flow-error").textContent;
            view.unmount();
            return message;
        };
        const messages = [
            await text("no_installation"),
            await text("code_rejected"),
            await text("github_unavailable", 503),
        ];
        expect(new Set(messages).size).toBe(3);
    });
});

describe("picking repositories and consenting", () => {
    const open = async () => {
        renderAt(CALLBACK_URL);
        await settle();
    };

    test("only repositories the server listed are offered", async () => {
        await open();
        const offered = CALLBACK.repositories.map((r) => r.full_name);
        for (const name of offered) expect(screen.getByTestId(`repo-${name}`)).toBeTruthy();
        expect(screen.getAllByRole("checkbox").length).toBe(offered.length + 1); // + consent
        expect(screen.queryByTestId("repo-acme/not-listed")).toBeNull();
    });

    test("Connect stays disabled until a repository is ticked AND consent is given", async () => {
        await open();
        const connect = screen.getByTestId("connector-connect-btn");
        expect(connect.disabled).toBe(true);

        fireEvent.click(screen.getByTestId("consent-checkbox"));
        expect(connect.disabled).toBe(true); // consent alone is not enough

        fireEvent.click(screen.getByTestId("consent-checkbox")); // back off
        fireEvent.click(screen.getByTestId("repo-acme/platform"));
        expect(connect.disabled).toBe(true); // a repo alone is not enough

        fireEvent.click(screen.getByTestId("consent-checkbox"));
        expect(connect.disabled).toBe(false);
        expect(mockCreate).not.toHaveBeenCalled();
    });

    test("create sends exactly the chosen repos, the consent flag and the ticket", async () => {
        await open();
        fireEvent.click(screen.getByTestId("repo-acme/docs"));
        fireEvent.click(screen.getByTestId("consent-checkbox"));
        await act(async () => { fireEvent.click(screen.getByTestId("connector-connect-btn")); });
        await settle();

        expect(mockCreate).toHaveBeenCalledTimes(1);
        const [workspaceId, body] = mockCreate.mock.calls[0];
        expect(workspaceId).toBe(CALLBACK.workspace_id);
        expect(body).toEqual({
            connector_type: "github",
            display_name: "GitHub: acme-admin",
            config: { repos: ["acme/docs"], consent_workspace_readable: true },
            ticket_id: CALLBACK.ticket_id,
        });
        // Success closes the panel and reloads the list from the server.
        await waitFor(() => expect(screen.queryByTestId("add-connector-flow")).toBeNull());
        expect(mockList.mock.calls.length).toBeGreaterThanOrEqual(2);
    });

    test("a failed create is shown, the panel stays, and the list is not reloaded as if it worked", async () => {
        mockCreate.mockRejectedValue({ status: 403, body: { error: { code: "SM002", details: { reason: "repository_not_authorized" } } } });
        await open();
        const listCalls = mockList.mock.calls.length;
        fireEvent.click(screen.getByTestId("repo-acme/platform"));
        fireEvent.click(screen.getByTestId("consent-checkbox"));
        await act(async () => { fireEvent.click(screen.getByTestId("connector-connect-btn")); });
        await settle();

        expect(screen.getByTestId("create-error").textContent).toMatch(/not covered by your GitHub authorization/i);
        expect(screen.getByTestId("repo-picker")).toBeTruthy();
        expect(mockList.mock.calls.length).toBe(listCalls);
        expect(screen.getByTestId("connector-connect-btn").disabled).toBe(false);
    });

    test("a duplicate name is reported as a name problem", async () => {
        mockCreate.mockRejectedValue({ status: 409, body: { error: { code: "SM010", details: { reason: "connector_exists" } } } });
        await open();
        fireEvent.click(screen.getByTestId("repo-acme/platform"));
        fireEvent.click(screen.getByTestId("consent-checkbox"));
        await act(async () => { fireEvent.click(screen.getByTestId("connector-connect-btn")); });
        await settle();
        expect(screen.getByTestId("create-error").textContent).toMatch(/already exists/i);
    });
});
