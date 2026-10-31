/** Wire contract of the invitation and role-change calls in the real API client. */

import { realApi, resetIdentityScopedCaches } from "./realApi";
import { setTokenGetter } from "./authToken";
import { setCurrentUserId } from "./currentUser";

const WS = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const USER = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const TOKEN = `smi_${"C".repeat(43)}`;
const UUID4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const reply = (body, status = 200) => ({
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    headers: { get: () => null },
    text: async () => (body === undefined ? "" : JSON.stringify(body)),
});

let calls;
beforeEach(() => {
    setTokenGetter(null);
    setCurrentUserId("user-1");
    window.localStorage.clear();
    resetIdentityScopedCaches();
    calls = [];
});
afterEach(() => {
    delete global.fetch;
    setCurrentUserId(null);
});

const answer = (body, status) => {
    global.fetch = jest.fn((url, init) => {
        calls.push({ url: String(url), init });
        return Promise.resolve(reply(body, status));
    });
};
const sent = () => calls[calls.length - 1];
const bodyOf = (call) => JSON.parse(call.init.body);

test("creating an invitation posts email and role with an idempotency key and returns the one-time token", async () => {
    answer({ data: { id: "i1", email: "a@x.co", role: "member", token: TOKEN, accept_path: `/invite/${TOKEN}` } }, 201);
    const created = await realApi.createInvitation(WS, { email: "a@x.co", role: "member" });
    expect(sent().url).toMatch(new RegExp(`/v1/workspaces/${WS}/invitations$`));
    expect(sent().init.method).toBe("POST");
    expect(bodyOf(sent())).toEqual({ email: "a@x.co", role: "member" });
    expect(sent().init.headers["Idempotency-Key"]).toMatch(UUID4);
    expect(created.token).toBe(TOKEN);
    // Naming the workspace explicitly means no workspace lookup happens first.
    expect(calls).toHaveLength(1);
});

test("listing returns the items", async () => {
    answer({ data: [{ id: "i1", email: "a@x.co", role: "viewer" }] });
    const r = await realApi.listInvitations(WS);
    expect(sent().url).toMatch(new RegExp(`/v1/workspaces/${WS}/invitations$`));
    expect(r.items).toHaveLength(1);
});

test("revoking sends DELETE for that invitation and accepts a 204", async () => {
    answer(undefined, 204);
    await realApi.revokeInvitation(WS, "inv-1");
    expect(sent().url).toMatch(new RegExp(`/v1/workspaces/${WS}/invitations/inv-1$`));
    expect(sent().init.method).toBe("DELETE");
    expect(sent().init.headers["Idempotency-Key"]).toMatch(UUID4);
});

test("accepting posts only the token", async () => {
    answer({ data: { workspace_id: WS, role: "member" } });
    const joined = await realApi.acceptInvitation(TOKEN);
    expect(sent().url).toMatch(/\/v1\/invitations\/accept$/);
    expect(sent().init.method).toBe("POST");
    expect(bodyOf(sent())).toEqual({ token: TOKEN });
    expect(joined).toMatchObject({ workspace_id: WS, role: "member" });
});

test("changing a role patches that member with just the role", async () => {
    answer({ data: { user: { id: USER }, role: "admin" } });
    await realApi.changeMemberRole(WS, USER, "admin");
    expect(sent().url).toMatch(new RegExp(`/v1/workspaces/${WS}/members/${USER}$`));
    expect(sent().init.method).toBe("PATCH");
    expect(bodyOf(sent())).toEqual({ role: "admin" });
    expect(sent().init.headers["Idempotency-Key"]).toMatch(UUID4);
});

test("a rejected change surfaces the server error to the caller", async () => {
    answer({ error: { code: "SM005", message: "A workspace must keep at least one founder." } }, 403);
    await expect(realApi.changeMemberRole(WS, USER, "member")).rejects.toMatchObject({ status: 403 });
});
