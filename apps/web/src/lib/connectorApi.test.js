/**
 * Wire contracts of the connector and source adapters in realApi: path, method,
 * query and body, as the backend's routes declare them. fetch is faked.
 */

process.env.REACT_APP_USE_MOCKS = "false";

const { realApi, resetIdentityScopedCaches } = require("./realApi");
const { setTokenGetter } = require("./authToken");
const { setCurrentUserId } = require("./currentUser");

const WS = "11111111-1111-4111-8111-111111111111";
const CID = "22222222-2222-4222-8222-222222222222";
let calls;

const reply = (body, status = 200) => ({
    ok: status < 400, status, statusText: String(status),
    headers: { get: () => null }, text: async () => JSON.stringify(body),
});

beforeEach(() => {
    calls = [];
    setTokenGetter(null);
    setCurrentUserId("user-a");
    global.fetch = jest.fn((url, init = {}) => {
        const u = new URL(String(url), "http://localhost");
        calls.push({ path: u.pathname, query: Object.fromEntries(u.searchParams), method: init.method || "GET", body: init.body ? JSON.parse(init.body) : undefined, headers: init.headers });
        if (u.pathname === "/v1/workspaces") return Promise.resolve(reply({ data: [{ id: WS, name: "W", slug: "w" }] }));
        return Promise.resolve(reply({}));
    });
});
afterEach(() => {
    resetIdentityScopedCaches();
    setCurrentUserId(null);
    delete global.fetch;
    delete process.env.REACT_APP_PILOT_MODE;
});

const last = () => calls[calls.length - 1];

test("DELETE sends remove_memories explicitly in both cases", async () => {
    await realApi.deleteConnector(CID, { remove_memories: false });
    expect(last()).toMatchObject({ method: "DELETE", path: `/v1/connectors/${CID}`, query: { remove_memories: "false" } });
    await realApi.deleteConnector(CID, { remove_memories: true });
    expect(last().query).toEqual({ remove_memories: "true" });
});

test("sync sends the required request body", async () => {
    await realApi.triggerSync(CID);
    expect(last()).toMatchObject({ method: "POST", path: `/v1/connectors/${CID}/sync`, body: { sync_type: "incremental" } });
});

test("status, retry-failed, rescan and sync-logs paths", async () => {
    await realApi.getConnectorStatus(CID);
    expect(last()).toMatchObject({ method: "GET", path: `/v1/connectors/${CID}/status` });
    await realApi.retryFailedConnector(CID);
    expect(last()).toMatchObject({ method: "POST", path: `/v1/connectors/${CID}/retry-failed` });
    await realApi.rescanConnector(CID);
    expect(last()).toMatchObject({ method: "POST", path: `/v1/connectors/${CID}/rescan` });
    await realApi.getSyncLogs(CID);
    expect(last()).toMatchObject({ method: "GET", path: `/v1/connectors/${CID}/sync-logs` });
});

test("pause and resume are a PATCH with only the status", async () => {
    await realApi.updateConnector(CID, { status: "paused" });
    expect(last()).toMatchObject({ method: "PATCH", path: `/v1/connectors/${CID}`, body: { status: "paused" } });
});

test("authorize is a workspace GET; the callback body carries code, state and installation_id only", async () => {
    await realApi.authorizeGithub(WS);
    expect(last()).toMatchObject({ method: "GET", path: `/v1/workspaces/${WS}/connectors/github/authorize` });
    await realApi.completeGithubCallback({ code: "c", state: "s", installation_id: 5 });
    expect(last()).toMatchObject({ method: "POST", path: "/v1/connectors/github/callback", body: { code: "c", state: "s", installation_id: 5 } });
    await realApi.completeGithubCallback({ code: "c", state: "s", installation_id: null });
    expect(last().body).toEqual({ code: "c", state: "s" });
});

test("create posts to the workspace the ticket belongs to", async () => {
    const payload = { connector_type: "github", display_name: "n", config: { repos: ["a/b"], consent_workspace_readable: true }, ticket_id: "t".repeat(16) };
    await realApi.createConnector(WS, payload);
    expect(last()).toMatchObject({ method: "POST", path: `/v1/workspaces/${WS}/connectors`, body: payload });
});

test("reapply posts the target and draft with an Idempotency-Key", async () => {
    await realApi.reapplyMemory("old-id", { target_memory_id: "t-1", content: "c", tags: [] });
    expect(last()).toMatchObject({ method: "POST", path: "/v1/memories/old-id/reapply", body: { target_memory_id: "t-1", content: "c", tags: [] } });
    expect(last().headers["Idempotency-Key"]).toBeTruthy();
});

test("normal search asks for the source; pilot search does not", async () => {
    await realApi.searchMemories({ query: "q" });
    expect(last().body).toEqual({ query: "q", mode: "hybrid", limit: 20, include_source: true });

    process.env.REACT_APP_PILOT_MODE = "true";
    await realApi.searchMemories({ query: "q" });
    expect(last().body).toEqual({ query: "q", mode: "hybrid", limit: 20 });
});
