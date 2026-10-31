/** Wire contract and wording of the audit/export calls (D-067). */
import { realApi, resetIdentityScopedCaches } from "./realApi";
import { setTokenGetter } from "./authToken";
import { setCurrentUserId } from "./currentUser";
import { describeAuditEvent } from "./auditEvents";
import { mapWorkspaceError } from "./workspaceErrors";

const WS = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const reply = (body, status = 200) => ({
    ok: status < 300, status, statusText: String(status),
    headers: { get: () => null }, text: async () => JSON.stringify(body),
});
let calls;
beforeEach(() => {
    setTokenGetter(null); setCurrentUserId("user-1"); window.localStorage.clear();
    resetIdentityScopedCaches(); calls = [];
    global.fetch = jest.fn((url, init) => { calls.push({ url: String(url), init }); return Promise.resolve(reply({ data: [{ id: 3 }] })); });
});
afterEach(() => { delete global.fetch; setCurrentUserId(null); });

test("audit: GET with limit and before_id, returns items", async () => {
    const r = await realApi.listWorkspaceAudit(WS, { beforeId: 9, limit: 50 });
    const url = new URL(calls[0].url);
    expect(url.pathname).toBe(`/v1/workspaces/${WS}/audit`);
    expect(url.searchParams.get("limit")).toBe("50");
    expect(url.searchParams.get("before_id")).toBe("9");
    expect(r.items).toEqual([{ id: 3 }]);
});

test("audit: first page sends no before_id", async () => {
    await realApi.listWorkspaceAudit(WS);
    expect(new URL(calls[0].url).searchParams.has("before_id")).toBe(false);
});

test("export: GET returns the document without the latency field", async () => {
    global.fetch = jest.fn(() => Promise.resolve(reply({ data: { export_schema_version: 1, memories: [] } })));
    const r = await realApi.exportWorkspace(WS);
    expect(r).toEqual({ export_schema_version: 1, memories: [] });
});

test("event sentences", () => {
    expect(describeAuditEvent({ event: "role_changed", actor_name: "Ana", target_name: "Bo", role_before: "member", role_after: "admin" }))
        .toBe("Ana changed Bo from Teammate to Admin");
    expect(describeAuditEvent({ event: "invitation_created", actor_name: "Ana", target_email: "c@x.io", role_after: "viewer" }))
        .toBe("Ana invited c@x.io as Viewer");
    expect(describeAuditEvent({ event: "workspace_exported", actor_user_id: "u" })).toBe("A former member exported the workspace data");
});

test("export errors are plain", () => {
    expect(mapWorkspaceError({ status: 413, body: { error: { code: "SM012" } } }, "export").kind).toBe("too_large");
    expect(mapWorkspaceError({ status: 429, retryAfterSeconds: 60 }, "export").message).toMatch(/60 seconds/);
    expect(mapWorkspaceError({ status: 500 }, "export").message).toMatch(/nothing was downloaded/);
});
