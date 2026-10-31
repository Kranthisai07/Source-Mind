/**
 * The setup step and the invite dialog against a stubbed API: what is sent,
 * that each outcome is shown on its own, and that nothing claims success it
 * did not get.
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

jest.mock("../../lib/api", () => ({
    __esModule: true,
    default: { createInvitation: jest.fn() },
    useMocks: false,
}));

import api from "../../lib/api";
import TeamSetupStep from "./TeamSetupStep";
import InviteDialog from "./InviteDialog";

const WS = { id: "11111111-1111-4111-8111-111111111111", name: "Acme" };
const created = (email, role) => ({
    id: `inv-${email}`, email, role, accept_path: `/invite/smi_${"B".repeat(43)}`,
});

beforeEach(() => {
    api.createInvitation.mockReset();
    Object.assign(navigator, { clipboard: { writeText: jest.fn().mockResolvedValue() } });
});

const type = (testId, value) => fireEvent.change(screen.getByTestId(testId), { target: { value } });

describe("team setup step", () => {
    test("sends one invitation per person in the role they were listed under", async () => {
        api.createInvitation.mockImplementation(async (_ws, { email, role }) => created(email, role));
        render(<TeamSetupStep workspace={WS} onFinish={jest.fn()} />);
        fireEvent.click(screen.getByTestId("admin-other"));
        type("admin-email", "Admin@X.co");
        type("setup-founders", "co@x.co");
        type("setup-teammates", "t1@x.co\nt2@x.co");
        fireEvent.click(screen.getByTestId("team-setup-submit"));

        await screen.findByTestId("team-setup-results");
        expect(api.createInvitation.mock.calls.map(([ws, body]) => [ws, body])).toEqual([
            [WS.id, { email: "co@x.co", role: "owner" }],
            [WS.id, { email: "admin@x.co", role: "admin" }],
            [WS.id, { email: "t1@x.co", role: "member" }],
            [WS.id, { email: "t2@x.co", role: "member" }],
        ]);
        expect(screen.getAllByTestId("invite-link")).toHaveLength(4);
    });

    test("holding admin yourself invites nobody as admin", async () => {
        api.createInvitation.mockImplementation(async (_ws, { email, role }) => created(email, role));
        render(<TeamSetupStep workspace={WS} onFinish={jest.fn()} />);
        type("setup-teammates", "t@x.co");
        fireEvent.click(screen.getByTestId("team-setup-submit"));
        await screen.findByTestId("team-setup-results");
        expect(api.createInvitation.mock.calls.every(([, b]) => b.role !== "admin")).toBe(true);
    });

    test("one failure is shown by name and does not hide the links that worked", async () => {
        api.createInvitation.mockImplementation(async (_ws, { email, role }) => {
            if (email === "bad@x.co") throw { status: 403, body: { error: { code: "SM005", message: "Only a founder can invite someone as admin or founder." } } };
            return created(email, role);
        });
        render(<TeamSetupStep workspace={WS} onFinish={jest.fn()} />);
        type("setup-teammates", "ok@x.co bad@x.co");
        fireEvent.click(screen.getByTestId("team-setup-submit"));
        await screen.findByTestId("team-setup-results");
        expect(screen.getAllByTestId("invite-link")).toHaveLength(1);
        expect(screen.getByTestId("team-setup-failure").textContent).toMatch(/bad@x\.co.*Only a founder/);
    });

    test("a bad address stops everything before anything is sent", () => {
        render(<TeamSetupStep workspace={WS} onFinish={jest.fn()} />);
        type("setup-teammates", "fine@x.co nonsense");
        fireEvent.click(screen.getByTestId("team-setup-submit"));
        expect(screen.getByTestId("team-setup-error").textContent).toMatch(/nonsense/);
        expect(api.createInvitation).not.toHaveBeenCalled();
    });

    test("choosing someone else as admin without an address is refused", () => {
        render(<TeamSetupStep workspace={WS} onFinish={jest.fn()} />);
        fireEvent.click(screen.getByTestId("admin-other"));
        fireEvent.click(screen.getByTestId("team-setup-submit"));
        expect(screen.getByTestId("team-setup-error")).toBeTruthy();
        expect(api.createInvitation).not.toHaveBeenCalled();
    });

    test("skipping and submitting an empty form both finish without calling the API", () => {
        const onFinish = jest.fn();
        render(<TeamSetupStep workspace={WS} onFinish={onFinish} />);
        fireEvent.click(screen.getByTestId("team-setup-skip"));
        fireEvent.click(screen.getByTestId("team-setup-submit"));
        expect(onFinish).toHaveBeenCalledTimes(2);
        expect(api.createInvitation).not.toHaveBeenCalled();
    });

    test("double submit sends each invitation once", async () => {
        let release;
        api.createInvitation.mockImplementation((_ws, { email, role }) => new Promise((resolve) => { release = () => resolve(created(email, role)); }));
        render(<TeamSetupStep workspace={WS} onFinish={jest.fn()} />);
        type("setup-teammates", "t@x.co");
        const form = screen.getByTestId("team-setup-form");
        fireEvent.submit(form);
        fireEvent.submit(form);
        expect(api.createInvitation).toHaveBeenCalledTimes(1);
        release();
        await screen.findByTestId("team-setup-results");
    });

    test("copy reports success only when the clipboard accepted the text", async () => {
        api.createInvitation.mockImplementation(async (_ws, { email, role }) => created(email, role));
        render(<TeamSetupStep workspace={WS} onFinish={jest.fn()} />);
        type("setup-teammates", "t@x.co");
        fireEvent.click(screen.getByTestId("team-setup-submit"));
        await screen.findByTestId("team-setup-results");
        fireEvent.click(screen.getByTestId("invite-link-copy"));
        await waitFor(() => expect(screen.getByTestId("invite-link-copy").textContent).toMatch(/Copied/));
        expect(navigator.clipboard.writeText).toHaveBeenCalledWith(expect.stringContaining("/invite/smi_"));

        navigator.clipboard.writeText.mockRejectedValueOnce(new Error("denied"));
        fireEvent.click(screen.getByTestId("invite-link-copy"));
        await screen.findByRole("alert");
    });
});

describe("invite dialog", () => {
    test("an admin is offered only teammate and viewer", () => {
        render(<InviteDialog open onOpenChange={jest.fn()} workspaceId={WS.id} callerRole="admin" />);
        const values = [...screen.getByTestId("invite-role").options].map((o) => o.value);
        expect(values).toEqual(["member", "viewer"]);
    });

    test("a founder is offered all four, and the link appears only after the 201", async () => {
        let release;
        api.createInvitation.mockImplementation((_ws, { email, role }) => new Promise((resolve) => { release = () => resolve(created(email, role)); }));
        render(<InviteDialog open onOpenChange={jest.fn()} workspaceId={WS.id} callerRole="owner" />);
        expect(screen.getByTestId("invite-role").options).toHaveLength(4);
        type("invite-email", "new@x.co");
        fireEvent.click(screen.getByTestId("invite-submit"));
        expect(screen.queryByTestId("invite-link")).toBeNull();
        release();
        expect(await screen.findByTestId("invite-link")).toBeTruthy();
    });

    test("a refusal is shown and no link is", async () => {
        api.createInvitation.mockRejectedValue({ status: 403, body: { error: { code: "SM005", message: "Administrator access is required." } } });
        render(<InviteDialog open onOpenChange={jest.fn()} workspaceId={WS.id} callerRole="owner" />);
        type("invite-email", "new@x.co");
        fireEvent.click(screen.getByTestId("invite-submit"));
        expect((await screen.findByTestId("invite-error")).textContent).toBe("Administrator access is required.");
        expect(screen.queryByTestId("invite-link")).toBeNull();
    });
});
