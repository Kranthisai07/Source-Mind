/**
 * Source provenance on memories: link, type, authorship, stale notice, and the
 * source_retired edit flow. Fixtures are SCHEMA-GENERATED from the backend
 * (MemoryResponse / SearchResponse / SourceRetiredPayload). Mocked API.
 */

import React from "react";
import { render, screen, act, fireEvent, within } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

import SEARCH from "./__fixtures__/searchResponse.withSource.json";
import RETIRED_BODY from "./__fixtures__/sourceRetired.error.json";

const mockGetMemory = jest.fn();
const mockGetVersions = jest.fn();
const mockUpdateMemory = jest.fn();
const mockReapply = jest.fn();

jest.mock("react-markdown", () => ({ __esModule: true, default: ({ children }) => children ?? null }));
jest.mock("remark-gfm", () => ({ __esModule: true, default: () => {} }));

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        getMemory: (...a) => mockGetMemory(...a),
        getMemoryVersions: (...a) => mockGetVersions(...a),
        updateMemory: (...a) => mockUpdateMemory(...a),
        reapplyMemory: (...a) => mockReapply(...a),
    },
}));

import MemoryCard from "../components/widgets/MemoryCard";
import MemoryDetail from "./MemoryDetail";
import { setCurrentUserId } from "../lib/currentUser";
import { loadDraft } from "../lib/editDrafts";

const [PR, COMMIT, COMMENT] = SEARCH.results.map((r) => r.memory);

const card = (memory, props = {}) =>
    render(<MemoryRouter><MemoryCard memory={memory} {...props} /></MemoryRouter>);

describe("memory card source line", () => {
    test("shows a link that opens in a new tab with rel=noopener noreferrer", () => {
        card(PR);
        const link = screen.getByTestId("card-source-link");
        expect(link.getAttribute("href")).toBe("https://github.com/acme/platform/pull/412");
        expect(link.getAttribute("target")).toBe("_blank");
        expect(link.getAttribute("rel")).toBe("noopener noreferrer");
        expect(link.textContent).toBe("GitHub pull request");
        expect(screen.getByTestId("card-source-revision").textContent).toBe("revision 1");
    });

    test("a resolved author is named as the writer, never as the importer", () => {
        card(PR);
        expect(screen.getByTestId("card-source-authorship").textContent).toBe("Written by priya");
        expect(document.body.textContent).not.toMatch(/imported by|synced by|connected by/i);
    });

    test("shared authorship lists the authors and shows no percentage", () => {
        card(COMMIT);
        const text = screen.getByTestId("card-source-authorship").textContent;
        expect(text).toBe("Multiple authors: marcus, sara");
        expect(screen.getByTestId("card-source-info").textContent).not.toContain("%");
    });

    test("an unresolved author is named as unresolved", () => {
        card(COMMENT);
        expect(screen.getByTestId("card-source-authorship").textContent).toBe("Unresolved author: external-dev");
    });

    test("a stale memory shows the notice and the newer link", () => {
        card(COMMIT);
        const notice = screen.getByTestId("card-source-stale");
        expect(notice.textContent).toMatch(/Source changed since this was imported/);
        expect(notice.textContent).toMatch(/edited after this was imported/);
        const newer = within(notice).getByTestId("card-source-newer-link");
        expect(newer.getAttribute("href")).toBe("https://github.com/acme/platform/commit/ab12cd4");
        expect(newer.getAttribute("rel")).toBe("noopener noreferrer");
    });

    test("a current memory shows no stale notice", () => {
        card(PR);
        expect(screen.queryByTestId("card-source-stale")).toBeNull();
    });

    test("a URL that is not https is shown as plain text, not a link", () => {
        card(COMMENT); // fixture url is a javascript: URL
        expect(screen.queryByTestId("card-source-link")).toBeNull();
        expect(screen.getByTestId("card-source-label").textContent).toBe("GitHub issue comment");
        expect(document.querySelector('a[href^="javascript:"]')).toBeNull();
    });

    test("http and malformed URLs are not linked either", () => {
        card({ ...PR, source: { ...PR.source, url: "http://github.com/x" } });
        expect(screen.queryByTestId("card-source-link")).toBeNull();
    });

    test("a memory with no source renders no source line", () => {
        card({ id: "plain", content: "No source here.", tags: [], created_at: "2026-10-01T00:00:00Z" });
        expect(screen.queryByTestId("card-source-info")).toBeNull();
    });

    test("clicking the source link does not also open the memory", () => {
        const navigated = jest.fn();
        render(
            <MemoryRouter initialEntries={["/"]}>
                <Routes>
                    <Route path="/" element={<MemoryCard memory={PR} />} />
                    <Route path="/memories/:id" element={<div data-testid="opened">{navigated()}</div>} />
                </Routes>
            </MemoryRouter>
        );
        fireEvent.click(screen.getByTestId("card-source-link"));
        expect(screen.queryByTestId("opened")).toBeNull();
    });

    test("pilot mode shows no source information", () => {
        card(PR, { pilotMode: true });
        expect(screen.queryByTestId("card-source-info")).toBeNull();
    });
});

// ── Detail page and the source_retired edit flow ────────────────────────────

const OLD = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const detail = (over = {}) => ({
    id: OLD, workspace_id: "w-1", content: "Original text.", tags: ["decision"], version: 1,
    created_at: "2026-09-01T00:00:00Z", attribution: [], is_current: true,
    latest_memory_id: null, supersession_reason: null, ...over,
});
const settle = async (ms = 100) => { await act(async () => { await new Promise((r) => setTimeout(r, ms)); }); };
const renderDetail = () =>
    render(
        <MemoryRouter initialEntries={[`/memories/${OLD}`]}>
            <Routes><Route path="/memories/:id" element={<MemoryDetail />} /></Routes>
        </MemoryRouter>
    );

beforeEach(() => {
    window.sessionStorage.clear();
    setCurrentUserId("user-a");
    mockGetMemory.mockReset().mockResolvedValue(detail());
    mockGetVersions.mockReset().mockResolvedValue({ versions: [], total: 0 });
    mockUpdateMemory.mockReset();
    mockReapply.mockReset().mockResolvedValue({ id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", version: 2 });
});
afterEach(() => { setCurrentUserId(null); });

describe("memory detail source", () => {
    test("shows source, authorship and the stale notice from the real shape", async () => {
        mockGetMemory.mockResolvedValue(detail({ source: COMMIT.source, authorship: COMMIT.authorship, source_state: COMMIT.source_state }));
        renderDetail();
        await settle();

        expect(screen.getByTestId("memory-source-link").getAttribute("rel")).toBe("noopener noreferrer");
        expect(screen.getByTestId("memory-source-revision").textContent).toBe("revision 2");
        expect(screen.getByTestId("memory-source-authorship").textContent).toBe("Multiple authors: marcus, sara");
        expect(screen.getByTestId("memory-source-stale")).toBeTruthy();
    });

    test("an unsafe URL on the detail page is not linked", async () => {
        mockGetMemory.mockResolvedValue(detail({ source: COMMENT.source, authorship: COMMENT.authorship }));
        renderDetail();
        await settle();
        expect(screen.queryByTestId("memory-source-link")).toBeNull();
        expect(screen.getByTestId("memory-source-authorship").textContent).toBe("Unresolved author: external-dev");
    });

    test("a memory without a source has no source section", async () => {
        renderDetail();
        await settle();
        expect(screen.queryByTestId("memory-source-section")).toBeNull();
    });
});

describe("a PATCH answered source_retired", () => {
    const retired = (candidates) => ({
        status: 409,
        body: { error: { ...RETIRED_BODY.error, details: { ...RETIRED_BODY.error.details, candidates } } },
    });
    const [C1, C2] = RETIRED_BODY.error.details.candidates;

    const editAndSave = async (text = "My careful edit.") => {
        fireEvent.click(await screen.findByTestId("mem-edit"));
        fireEvent.change(screen.getByTestId("memory-edit-content"), { target: { value: text } });
        await act(async () => { fireEvent.click(screen.getByTestId("memory-edit-save")); });
        await settle();
    };

    test("candidates are offered as an explicit choice and none is selected", async () => {
        mockUpdateMemory.mockRejectedValue({ status: 409, body: RETIRED_BODY });
        renderDetail();
        await editAndSave();

        const banner = screen.getByTestId("memory-edit-source-retired");
        expect(banner.textContent).toMatch(/Nothing is chosen for you/);
        expect(banner.textContent).toContain(C1.preview);
        expect(banner.textContent).toContain(C2.preview);
        expect(screen.getByTestId(`reapply-candidate-${C1.memory_id}`).checked).toBe(false);
        expect(screen.getByTestId(`reapply-candidate-${C2.memory_id}`).checked).toBe(false);
        expect(screen.getByTestId("memory-reapply").disabled).toBe(true);
        expect(mockReapply).not.toHaveBeenCalled();
        expect(screen.getByTestId("memory-retired-newer-link").getAttribute("rel")).toBe("noopener noreferrer");
    });

    test("re-apply posts the draft to the chosen candidate only", async () => {
        mockUpdateMemory.mockRejectedValue({ status: 409, body: RETIRED_BODY });
        renderDetail();
        await editAndSave("My careful edit.");

        fireEvent.click(screen.getByTestId(`reapply-candidate-${C2.memory_id}`));
        expect(screen.getByTestId("memory-reapply").disabled).toBe(false);
        await act(async () => { fireEvent.click(screen.getByTestId("memory-reapply")); });
        await settle();

        expect(mockReapply).toHaveBeenCalledTimes(1);
        const [retiredId, payload] = mockReapply.mock.calls[0];
        expect(retiredId).toBe(OLD);
        expect(payload).toMatchObject({ target_memory_id: C2.memory_id, content: "My careful edit." });
        expect(typeof payload.idempotencyKey).toBe("string");
        // The PATCH is not retried by re-applying.
        expect(mockUpdateMemory).toHaveBeenCalledTimes(1);
    });

    test("a failed re-apply keeps the draft and says nothing was saved", async () => {
        mockUpdateMemory.mockRejectedValue({ status: 409, body: RETIRED_BODY });
        mockReapply.mockRejectedValue({ status: 409, body: { error: { code: "no_replacement", message: "none" } } });
        renderDetail();
        await editAndSave("Draft to keep.");

        fireEvent.click(screen.getByTestId(`reapply-candidate-${C1.memory_id}`));
        await act(async () => { fireEvent.click(screen.getByTestId("memory-reapply")); });
        await settle();

        expect(screen.getByTestId("memory-reapply-error").textContent).toMatch(/Nothing was saved.*draft is kept/i);
        expect(screen.getByTestId("memory-edit-content").value).toBe("Draft to keep.");
        expect(loadDraft("user-a", OLD)).toMatchObject({ content: "Draft to keep." });
    });

    test("with zero candidates it says so plainly, keeps the draft, and offers no re-apply", async () => {
        mockUpdateMemory.mockRejectedValue(retired([]));
        renderDetail();
        await editAndSave("Nothing to apply this to.");

        expect(screen.getByTestId("memory-retired-no-candidates").textContent).toMatch(/no replacement memory/i);
        expect(screen.queryByTestId("memory-reapply")).toBeNull();
        expect(screen.getByTestId("memory-edit-content").value).toBe("Nothing to apply this to.");
        expect(loadDraft("user-a", OLD)).toMatchObject({ content: "Nothing to apply this to." });
    });

    test("no_replacement on the PATCH keeps the draft and names the situation", async () => {
        mockUpdateMemory.mockRejectedValue({ status: 409, body: { error: { code: "no_replacement", message: "none" } } });
        renderDetail();
        await editAndSave("Orphaned edit.");

        expect(screen.getByTestId("memory-edit-no-replacement").textContent).toMatch(/no replacement memory/i);
        expect(screen.getByTestId("memory-edit-content").value).toBe("Orphaned edit.");
    });

    test("an unsafe newer_source_url is not linked", async () => {
        mockUpdateMemory.mockRejectedValue({
            status: 409,
            body: { error: { code: "source_retired", details: { reason: "source_deleted", newer_source_url: "javascript:alert(1)", candidates: [] } } },
        });
        renderDetail();
        await editAndSave();
        expect(screen.queryByTestId("memory-retired-newer-link")).toBeNull();
    });
});
