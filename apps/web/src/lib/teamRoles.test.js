import { ALL_ROLES, assignableRoles, invitableRoles, roleLabel } from "./roles";
import { buildInvitePlan, MAX_SETUP_INVITES, parseEmails } from "./teamSetup";
import { INVITE_TOKEN_RE, safeNextPath } from "./inviteToken";
import { mapWorkspaceError } from "./workspaceErrors";

const TOKEN = `smi_${"A".repeat(43)}`;

describe("role names and what to offer", () => {
    test("stored roles are shown as founder, admin, teammate, viewer", () => {
        expect(ALL_ROLES.map(roleLabel)).toEqual(["Founder", "Admin", "Teammate", "Viewer"]);
    });

    test("founders may invite as anything, admins only as teammate or viewer, others as nothing", () => {
        expect(invitableRoles("owner")).toEqual(ALL_ROLES);
        expect(invitableRoles("admin")).toEqual(["member", "viewer"]);
        expect(invitableRoles("member")).toEqual([]);
        expect(invitableRoles("viewer")).toEqual([]);
        expect(invitableRoles(undefined)).toEqual([]);
    });

    test("an admin is never offered admin or founder, and never offered a change to an admin or founder", () => {
        for (const target of ALL_ROLES) {
            const offered = assignableRoles("admin", target);
            expect(offered).not.toContain("admin");
            expect(offered).not.toContain("owner");
            if (target === "admin" || target === "owner") expect(offered).toEqual([]);
        }
        expect(assignableRoles("admin", "member")).toEqual(["viewer"]);
    });

    test("founders are offered every other role; teammates and viewers none", () => {
        expect(assignableRoles("owner", "admin")).toEqual(["owner", "member", "viewer"]);
        expect(assignableRoles("member", "viewer")).toEqual([]);
        expect(assignableRoles("viewer", "member")).toEqual([]);
    });
});

describe("turning the setup answers into invitations", () => {
    test("splits on commas, spaces and newlines, lowercases, and drops repeats", () => {
        expect(parseEmails("A@x.co, b@x.co\nB@X.CO;  c@x.co").valid).toEqual(["a@x.co", "b@x.co", "c@x.co"]);
    });

    test("reports what is not an address instead of dropping it silently", () => {
        const { valid, invalid } = parseEmails("good@x.co nope @x.co a@b");
        expect(valid).toEqual(["good@x.co"]);
        expect(invalid).toEqual(["nope", "@x.co", "a@b"]);
    });

    test("one address listed twice gets the higher authority", () => {
        const plan = buildInvitePlan({ admin: "a@x.co", founders: "a@x.co\nf@x.co", teammates: "a@x.co t@x.co" });
        expect(plan.invites).toEqual([
            { email: "a@x.co", role: "owner" },
            { email: "f@x.co", role: "owner" },
            { email: "t@x.co", role: "member" },
        ]);
    });

    test("an empty form plans nothing, and a long one is flagged", () => {
        expect(buildInvitePlan({}).invites).toEqual([]);
        const many = Array.from({ length: MAX_SETUP_INVITES + 1 }, (_, i) => `u${i}@x.co`).join("\n");
        expect(buildInvitePlan({ teammates: many }).tooMany).toBe(true);
    });
});

describe("invitation links", () => {
    test("the token shape matches the server's", () => {
        expect(INVITE_TOKEN_RE.test(TOKEN)).toBe(true);
        for (const bad of ["", "smi_short", `${TOKEN}x`, `smk_${"A".repeat(43)}`, ` ${TOKEN}`]) {
            expect(INVITE_TOKEN_RE.test(bad)).toBe(false);
        }
    });

    test("sign-in may return only to an invitation page", () => {
        expect(safeNextPath(`/invite/${TOKEN}`)).toBe(`/invite/${TOKEN}`);
        for (const bad of ["https://evil.example", "//evil.example", "/dashboard", `/invite/${TOKEN}/x`, `/invite/${TOKEN}?a=1`, "/invite/smi_short", null, undefined]) {
            expect(safeNextPath(bad)).toBeNull();
        }
    });
});

describe("messages for invitations and role changes", () => {
    const err = (status, code, message, details) => ({ status, body: { error: { code, message, details } } });

    test("an unusable invitation gives one message whatever the cause", () => {
        const a = mapWorkspaceError(err(404, "SM056"), "acceptInvite");
        const b = mapWorkspaceError(err(404, undefined), "acceptInvite");
        expect(a.kind).toBe("invitation_unusable");
        expect(b.message).toBe(a.message);
    });

    test("a refused change shows the server's own sentence", () => {
        const m = mapWorkspaceError(err(403, "SM005", "A workspace must keep at least one founder."), "changeRole");
        expect(m.message).toBe("A workspace must keep at least one founder.");
    });

    test("a missing route is reported as unsupported, not as not-found", () => {
        expect(mapWorkspaceError(err(404), "invite").kind).toBe("unsupported");
        expect(mapWorkspaceError(err(405), "changeRole").kind).toBe("unsupported");
    });

    test("an unknown failure never claims success", () => {
        expect(mapWorkspaceError(err(500), "invite").message).toMatch(/unconfirmed/);
    });
});
