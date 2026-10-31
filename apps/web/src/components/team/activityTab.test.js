import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ActivityTab from "./ActivityTab";
import api from "../../lib/api";

jest.mock("sonner", () => ({ toast: { success: jest.fn() } }));
jest.mock("../../lib/api", () => ({ __esModule: true, default: { listWorkspaceAudit: jest.fn(), exportWorkspace: jest.fn() } }));

const ev = (id) => ({ id, event: "workspace_exported", actor_name: "Ana", at: "2026-10-31T10:00:00Z" });

beforeEach(() => {
    jest.clearAllMocks();
    global.URL.createObjectURL = jest.fn(() => "blob:x");
    global.URL.revokeObjectURL = jest.fn();
    jest.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});

test("lists events and loads the next page from the last id", async () => {
    api.listWorkspaceAudit.mockResolvedValueOnce({ items: Array.from({ length: 50 }, (_, i) => ev(100 - i)) });
    api.listWorkspaceAudit.mockResolvedValueOnce({ items: [ev(50)] });
    render(<ActivityTab workspaceId="w1" />);
    await screen.findByTestId("activity-100");
    fireEvent.click(screen.getByTestId("activity-more"));
    await screen.findByTestId("activity-50");
    expect(api.listWorkspaceAudit).toHaveBeenLastCalledWith("w1", { beforeId: 51, limit: 50 });
    expect(screen.queryByTestId("activity-more")).toBeNull();
});

test("export downloads a file; a failure is shown and nothing downloads", async () => {
    api.listWorkspaceAudit.mockResolvedValue({ items: [] });
    api.exportWorkspace.mockResolvedValueOnce({ memories: [] });
    render(<ActivityTab workspaceId="w1" />);
    await screen.findByTestId("activity-empty");
    fireEvent.click(screen.getByTestId("export-btn"));
    await waitFor(() => expect(global.URL.createObjectURL).toHaveBeenCalledTimes(1));

    api.exportWorkspace.mockRejectedValueOnce({ status: 413, body: { error: { code: "SM012" } } });
    fireEvent.click(screen.getByTestId("export-btn"));
    await screen.findByTestId("export-error");
    expect(global.URL.createObjectURL).toHaveBeenCalledTimes(1);
});
