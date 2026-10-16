import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import VARIANTS from "./__fixtures__/mcpKeys.variants.json";

const mockList = jest.fn();
const mockCreate = jest.fn();
const mockRevoke = jest.fn();

jest.mock("../lib/api", () => ({
    __esModule: true,
    useMocks: true,
    default: {
        listMcpKeys: (...args) => mockList(...args),
        createMcpKey: (...args) => mockCreate(...args),
        revokeMcpKey: (...args) => mockRevoke(...args),
    },
}));

import McpKeysSection from "../components/settings/McpKeysSection";

const SECRET = "smk_" + "Zq7".repeat(14) + "Z";
const CREATED = {
    id: "44444444-4444-4444-8444-444444444444",
    name: "Cursor on desk",
    key: SECRET,
    key_prefix: SECRET.slice(0, 8),
    expires_at: "2027-01-14T10:00:00Z",
    created_at: "2026-10-16T10:00:00Z",
};
const ACTIVE_ID = "11111111-1111-4111-8111-111111111111";
const EXPIRED_ID = "22222222-2222-4222-8222-222222222222";
const REVOKED_ID = "33333333-3333-4333-8333-333333333333";

function apiError(status, extra = {}) {
    const error = new Error(`HTTP ${status}`);
    error.status = status;
    return Object.assign(error, extra);
}

async function renderLoaded(variant = "populated") {
    mockList.mockResolvedValue(VARIANTS[variant]);
    const view = render(<McpKeysSection />);
    await waitFor(() => expect(screen.queryByTestId("mcp-keys-loading")).toBeNull());
    return view;
}

async function createKey({ name = "Cursor on desk", days = null } = {}) {
    fireEvent.click(await screen.findByTestId("mcp-keys-create-btn"));
    fireEvent.change(await screen.findByTestId("mcp-key-name-input"), { target: { value: name } });
    if (days) fireEvent.change(screen.getByTestId("mcp-key-expiry-select"), { target: { value: String(days) } });
    fireEvent.click(screen.getByTestId("mcp-key-create-submit"));
}

beforeEach(() => {
    mockList.mockReset();
    mockCreate.mockReset();
    mockRevoke.mockReset();
    Object.assign(navigator, { clipboard: { writeText: jest.fn().mockResolvedValue(undefined) } });
});

afterEach(() => {
    jest.restoreAllMocks();
});

describe("MCP keys list", () => {
    test("renders every status with the prefix only, creators, and revoke only on active keys", async () => {
        await renderLoaded();
        expect(screen.getByTestId(`mcp-key-status-${ACTIVE_ID}`).textContent).toContain("active");
        expect(screen.getByTestId(`mcp-key-status-${EXPIRED_ID}`).textContent).toContain("expired");
        expect(screen.getByTestId(`mcp-key-status-${REVOKED_ID}`).textContent).toContain("revoked");
        const row = screen.getByTestId(`mcp-key-row-${ACTIVE_ID}`);
        expect(
            within(row).getByText((_, el) => el.tagName === "TD" && el.textContent === "smk_Ab3x…")
        ).not.toBeNull();
        expect(within(screen.getByTestId(`mcp-key-row-${EXPIRED_ID}`)).getByText("never")).not.toBeNull();
        expect(screen.getByText("Created by")).not.toBeNull(); // two creators in the fixture
        expect(screen.getByTestId(`mcp-key-revoke-${ACTIVE_ID}`)).not.toBeNull();
        expect(screen.queryByTestId(`mcp-key-revoke-${EXPIRED_ID}`)).toBeNull();
        expect(screen.queryByTestId(`mcp-key-revoke-${REVOKED_ID}`)).toBeNull();
    });

    test("states plainly what a key can do and claims no client is verified", async () => {
        await renderLoaded();
        const text = screen.getByTestId("mcp-keys-section").textContent;
        expect(text).toMatch(/read this workspace's memories as you/i);
        expect(text).toMatch(/read-only/i);
        expect(text).not.toMatch(/verified|certified|supported clients/i);
    });

    test("empty state", async () => {
        await renderLoaded("empty");
        expect(screen.getByTestId("mcp-keys-empty")).not.toBeNull();
        expect(screen.queryByTestId("mcp-keys-table")).toBeNull();
    });

    test("shows a banner when MCP is not enabled on the deployment", async () => {
        await renderLoaded("disabled");
        expect(screen.getByTestId("mcp-disabled-banner").textContent).toMatch(/not enabled on this deployment/i);
    });

    test("an error shows the error state, no table and no fabricated empty state", async () => {
        mockList.mockRejectedValue(apiError(403));
        render(<McpKeysSection />);
        expect(await screen.findByTestId("mcp-keys-error")).not.toBeNull();
        expect(screen.queryByTestId("mcp-keys-table")).toBeNull();
        expect(screen.queryByTestId("mcp-keys-empty")).toBeNull();
    });
});

describe("creating a key", () => {
    test("sends the trimmed name and chosen expiry, shows the key once, and it is gone after Close", async () => {
        mockCreate.mockResolvedValue(CREATED);
        await renderLoaded();
        await createKey({ name: "  Cursor on desk  ", days: 365 });

        const secretInput = await screen.findByTestId("mcp-key-secret");
        expect(secretInput.value).toBe(SECRET);
        expect(mockCreate).toHaveBeenCalledWith(undefined, { name: "Cursor on desk", expires_in_days: 365 });
        expect(screen.getByTestId("mcp-key-secret-dialog").textContent).toMatch(/only time the key is shown/i);
        await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2)); // list refreshed

        fireEvent.click(screen.getByTestId("mcp-key-secret-close"));
        await waitFor(() => expect(screen.queryByTestId("mcp-key-secret")).toBeNull());
        expect(document.body.textContent).not.toContain(SECRET);
        expect(screen.queryByDisplayValue(SECRET)).toBeNull();
    });

    test("copies the key and reports success, and reports a failed copy honestly", async () => {
        mockCreate.mockResolvedValue(CREATED);
        await renderLoaded();
        await createKey();
        await screen.findByTestId("mcp-key-secret");

        fireEvent.click(screen.getByTestId("mcp-key-copy"));
        expect((await screen.findByTestId("mcp-key-copy-status")).textContent).toContain("Copied");
        expect(navigator.clipboard.writeText).toHaveBeenCalledWith(SECRET);

        navigator.clipboard.writeText.mockRejectedValueOnce(new Error("denied"));
        fireEvent.click(screen.getByTestId("mcp-key-copy"));
        await waitFor(() =>
            expect(screen.getByTestId("mcp-key-copy-status").textContent).toMatch(/copy failed/i)
        );
    });

    test("validates the name (empty and over 100 characters) without calling the API", async () => {
        await renderLoaded();
        fireEvent.click(await screen.findByTestId("mcp-keys-create-btn"));
        fireEvent.click(await screen.findByTestId("mcp-key-create-submit"));
        expect(await screen.findByTestId("mcp-key-name-error")).not.toBeNull();
        fireEvent.change(screen.getByTestId("mcp-key-name-input"), { target: { value: "x".repeat(101) } });
        fireEvent.click(screen.getByTestId("mcp-key-create-submit"));
        expect(screen.getByTestId("mcp-key-name-error")).not.toBeNull();
        expect(mockCreate).not.toHaveBeenCalled();
    });

    test("a 429 gets its own message, no key is shown, and the form stays open", async () => {
        mockCreate.mockRejectedValue(apiError(429, { retryAfterSeconds: 60 }));
        await renderLoaded();
        await createKey();
        expect((await screen.findByTestId("mcp-key-create-error")).textContent).toMatch(/too many keys/i);
        expect(screen.queryByTestId("mcp-key-secret")).toBeNull();
        expect(screen.getByTestId("mcp-key-create-dialog")).not.toBeNull();
    });

    test("never writes the key to storage or the console", async () => {
        const stored = [];
        jest.spyOn(Storage.prototype, "setItem").mockImplementation((...args) => { stored.push(args); });
        const consoleCalls = [];
        ["log", "info", "warn", "error", "debug"].forEach((method) =>
            jest.spyOn(console, method).mockImplementation((...args) => { consoleCalls.push(args); })
        );
        mockCreate.mockResolvedValue(CREATED);
        await renderLoaded();
        await createKey();
        await screen.findByTestId("mcp-key-secret");
        fireEvent.click(screen.getByTestId("mcp-key-copy"));
        await screen.findByTestId("mcp-key-copy-status");
        fireEvent.click(screen.getByTestId("mcp-key-secret-close"));

        const dump = JSON.stringify([stored, consoleCalls.map((call) => call.map(String))]);
        expect(dump).not.toContain(SECRET);
        expect(window.location.href).not.toContain(SECRET);
    });
});

describe("revoking a key", () => {
    test("asks in-page, then calls the API and refreshes the list", async () => {
        const confirmSpy = jest.spyOn(window, "confirm").mockReturnValue(true);
        mockRevoke.mockResolvedValue({});
        await renderLoaded();
        fireEvent.click(screen.getByTestId(`mcp-key-revoke-${ACTIVE_ID}`));
        const dialog = await screen.findByTestId("mcp-key-revoke-dialog");
        expect(dialog.textContent).toMatch(/stop working on their next call/i);
        expect(mockRevoke).not.toHaveBeenCalled();

        fireEvent.click(screen.getByTestId("mcp-key-revoke-confirm"));
        await waitFor(() => expect(mockRevoke).toHaveBeenCalledWith(undefined, ACTIVE_ID));
        await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));
        await waitFor(() => expect(screen.queryByTestId("mcp-key-revoke-dialog")).toBeNull());
        expect(confirmSpy).not.toHaveBeenCalled(); // the app shows no native dialogs
    });

    test("cancel makes no call", async () => {
        await renderLoaded();
        fireEvent.click(screen.getByTestId(`mcp-key-revoke-${ACTIVE_ID}`));
        fireEvent.click(await screen.findByTestId("mcp-key-revoke-cancel"));
        await waitFor(() => expect(screen.queryByTestId("mcp-key-revoke-dialog")).toBeNull());
        expect(mockRevoke).not.toHaveBeenCalled();
    });

    test("a failed revoke keeps the dialog open with the reason", async () => {
        mockRevoke.mockRejectedValue(apiError(404));
        await renderLoaded();
        fireEvent.click(screen.getByTestId(`mcp-key-revoke-${ACTIVE_ID}`));
        fireEvent.click(await screen.findByTestId("mcp-key-revoke-confirm"));
        expect(await screen.findByTestId("mcp-key-revoke-error")).not.toBeNull();
        expect(screen.getByTestId("mcp-key-revoke-dialog")).not.toBeNull();
    });
});
