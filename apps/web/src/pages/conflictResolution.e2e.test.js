/**
 * Conflict resolution, driven through the real page against a real API.
 *
 * Every earlier check of this feature asserted against a belief about the
 * backend. Two of those beliefs were wrong — a note that Pydantic dropped, a
 * split rule that did not exist — and neither produced a failure anywhere. So
 * this renders the actual ConflictDetail component, types into the actual
 * inputs, clicks the actual button, and then reads the database directly to
 * see what was stored.
 *
 * WHAT IS REAL HERE
 *   - the page component, its validation, its payload construction, its error
 *     handling, and the realApi adapter: all the shipped code, unmodified
 *   - the API, the resolver, and PostgreSQL with RLS in force, running as
 *     sourcemind_test (NOSUPERUSER NOBYPASSRLS)
 *   - assertions read committed rows, not the HTTP response
 *
 * WHAT IS NOT REAL, AND SO IS NOT EVIDENCE
 *   - No browser. jsdom renders the component; `fetch` is a shim over Node's
 *     http (below). CSS, layout and real event dispatch are not exercised.
 *   - No Clerk. The API runs with the validated development-only
 *     AUTH_DEV_BYPASS_ENABLED flag, so token handling is NOT covered here.
 *   - Embeddings are stubbed (see openai_stub.py). The `merged` path calls a
 *     model; the stub returns a deterministic vector, which proves the call is
 *     made and its result stored, and proves nothing about embedding quality.
 *     Conflict DETECTION, which depends on real embeddings, is out of scope.
 *
 * Skipped unless E2E_API_URL is set, so it never runs in CI or an ordinary
 * `npm test` — it needs the disposable stack described in
 * docs/handoff/RESOURCE_LEASES.md.
 */

import React from "react";
import http from "http";
const { createPsql } = require("../../e2e/psqlClient");
import { render, screen, act, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

const API = process.env.E2E_API_URL;
const describeE2E = API ? describe : describe.skip;

/** Fixture ids, seeded by scratchpad/seed_conflicts.sql. One per action. */
const C = {
    merged: "00000000-0000-4000-8000-0000000c0001",
    split: "00000000-0000-4000-8000-0000000c0002",
    deferred: "00000000-0000-4000-8000-0000000c0003",
    missing: "00000000-0000-4000-8000-0000000cffff",
};
/** The identity AUTH_DEV_BYPASS_ENABLED pins, in core/dependencies.py. */
const DEV_USER = "00000000-0000-4000-8000-000000000001";

const M = {
    splitA: "00000000-0000-4000-8000-00000000a002",
    splitB: "00000000-0000-4000-8000-00000000b002",
    mergedA: "00000000-0000-4000-8000-00000000a001",
    mergedB: "00000000-0000-4000-8000-00000000b001",
};

/**
 * Minimal fetch over Node's http, covering exactly what realApi's `request`
 * uses: method, headers, body, and a response with ok/status/statusText/
 * headers.get/text. jsdom ships no fetch, and a browser fetch is not available
 * in this environment — this is the shim disclosed in the header.
 */
function nodeFetch(url, init = {}) {
    return new Promise((resolve, reject) => {
        const u = new URL(String(url));
        const req = http.request(
            {
                hostname: u.hostname,
                port: u.port,
                path: u.pathname + u.search,
                method: init.method || "GET",
                headers: init.headers || {},
            },
            (res) => {
                let body = "";
                res.setEncoding("utf8");
                res.on("data", (d) => { body += d; });
                res.on("end", () =>
                    resolve({
                        ok: res.statusCode >= 200 && res.statusCode < 300,
                        status: res.statusCode,
                        statusText: res.statusMessage || "",
                        headers: { get: (k) => res.headers[String(k).toLowerCase()] ?? null },
                        text: async () => body,
                    })
                );
            }
        );
        req.on("error", reject);
        if (init.body) req.write(init.body);
        req.end();
    });
}

/**
 * Read committed rows out of the disposable cluster.
 *
 * Built lazily rather than at module scope: createPsql throws when the
 * disposable guard is not satisfied, and that must surface as a failing test
 * with a readable message, not as a module-load crash that would also take
 * out the skip path on a machine with no stack running.
 */
let _sql = null;
function sql(query) {
    if (_sql === null) _sql = createPsql();
    return _sql(query);
}

const row = (q) => sql(q).split("|");

function Page({ id }) {
    // Required lazily: lib/api reads REACT_APP_USE_MOCKS when the module is
    // first evaluated, and the runner sets it to "false" on the command line.
    const ConflictDetail = require("./ConflictDetail").default;
    return (
        <MemoryRouter initialEntries={[`/conflicts/${id}`]}>
            <Routes>
                <Route path="/conflicts/:id" element={<ConflictDetail />} />
            </Routes>
        </MemoryRouter>
    );
}

async function openConflict(id) {
    render(<Page id={id} />);
    await waitFor(() => expect(screen.queryByRole("status")).toBeNull(), {
        timeout: 10000,
    });
}

const click = (el) => act(async () => { fireEvent.click(el); });
const type = (el, value) => act(async () => {
    fireEvent.change(el, { target: { value } });
});

describeE2E("conflict resolution against the real API", () => {
    // Real HTTP, a real resolver and a real database. The `merged` path alone
    // writes a memory, an embedding, two attribution rows and two relations,
    // which comfortably exceeds Jest's 5s default.
    jest.setTimeout(30000);

    beforeAll(() => {
        global.fetch = nodeFetch;
    });

    afterEach(() => {
        jest.restoreAllMocks();
    });

    // ── merged ────────────────────────────────────────────────────────────
    describe("merged", () => {
        const MERGED_TEXT =
            "Vector index is HNSW (m=16, ef_construction=64); IVFFlat was the superseded choice.";
        const NOTE = "Merged: HNSW supersedes IVFFlat, both statements kept in one.";

        test("stores the merged content, the note, and resolves the conflict", async () => {
            await openConflict(C.merged);

            await click(screen.getByText("Merge both"));
            await type(screen.getByTestId("merged-content"), MERGED_TEXT);
            await type(screen.getByTestId("resolve-note"), NOTE);
            await click(screen.getByTestId("confirm-resolution"));

            await waitFor(() => {
                const [status] = row(
                    `SELECT status FROM memory_conflicts WHERE id = '${C.merged}'`
                );
                expect(status).toBe("resolved");
            }, { timeout: 10000 });

            const [status, note, resolver, resolvedAt, blocks] = row(
                `SELECT status, resolution_note, resolver_id::text, ` +
                `resolved_at IS NOT NULL, blocks_derivation ` +
                `FROM memory_conflicts WHERE id = '${C.merged}'`
            );
            expect(status).toBe("resolved");
            expect(note).toBe(NOTE);
            expect(resolver).toBe(DEV_USER);
            expect(resolvedAt).toBe("t");
            // A settled conflict never blocks, whatever severity it carried.
            expect(blocks).toBe("f");
        });

        test("the merged memory exists, carries the typed content, and is embedded", async () => {
            const [id, content, hasEmbedding] = row(
                `SELECT id::text, content, embedding IS NOT NULL FROM memories ` +
                `WHERE content = ${JSON.stringify(MERGED_TEXT).replace(/"/g, "'")}`
            );
            expect(id).toBeTruthy();
            expect(content).toBe(MERGED_TEXT);
            // Via the stub, not a real model — see the header.
            expect(hasEmbedding).toBe("t");
        });

        test("both source memories are retired, not deleted", async () => {
            const a = row(`SELECT current_version, content FROM memories WHERE id = '${M.mergedA}'`);
            const b = row(`SELECT current_version, content FROM memories WHERE id = '${M.mergedB}'`);
            expect(a[0]).toBe("f");
            expect(b[0]).toBe("f");
            // Still readable: a merge supersedes, it does not destroy history.
            expect(a[1]).toContain("IVFFlat");
            expect(b[1]).toContain("HNSW");
        });
    });

    // ── split ─────────────────────────────────────────────────────────────
    describe("split", () => {
        const NOTE = "Split: these are per-environment retention policies, not a contradiction.";

        test("ADDS a tag to each memory, preserving the tags already there", async () => {
            // Seeded as {retention,staging} and {retention}. If resolution
            // replaced tags rather than appending, those would be gone.
            expect(sql(`SELECT array_to_string(tags, ',') FROM memories WHERE id = '${M.splitA}'`))
                .toBe("retention,staging");
            expect(sql(`SELECT array_to_string(tags, ',') FROM memories WHERE id = '${M.splitB}'`))
                .toBe("retention");

            await openConflict(C.split);
            await click(screen.getByText("Split by tag"));
            await type(screen.getByTestId("tag-a"), "env-staging");
            await type(screen.getByTestId("tag-b"), "env-production");
            await type(screen.getByTestId("resolve-note"), NOTE);
            await click(screen.getByTestId("confirm-resolution"));

            await waitFor(() => {
                expect(sql(`SELECT status FROM memory_conflicts WHERE id = '${C.split}'`))
                    .toBe("resolved");
            }, { timeout: 10000 });

            expect(sql(`SELECT array_to_string(tags, ',') FROM memories WHERE id = '${M.splitA}'`))
                .toBe("retention,staging,env-staging");
            expect(sql(`SELECT array_to_string(tags, ',') FROM memories WHERE id = '${M.splitB}'`))
                .toBe("retention,env-production");
            expect(sql(`SELECT resolution_note FROM memory_conflicts WHERE id = '${C.split}'`))
                .toBe(NOTE);
        });

        test("both memories stay current — a split keeps both claims", async () => {
            expect(sql(`SELECT current_version FROM memories WHERE id = '${M.splitA}'`)).toBe("t");
            expect(sql(`SELECT current_version FROM memories WHERE id = '${M.splitB}'`)).toBe("t");
        });

        test("identical tags are accepted, as the resolver allows", async () => {
            // The form used to refuse this. The backend never did.
            const { validateResolution } = require("../lib/conflictResolution");
            const { ok } = validateResolution("split", { tagA: "same", tagB: "same" });
            expect(ok).toBe(true);
        });
    });

    // ── deferred ──────────────────────────────────────────────────────────
    describe("deferred", () => {
        const NOTE = "Deferred: waiting on the platform team to confirm the gating policy.";
        const LOCAL = "2026-12-24T09:30";

        test("stores the revisit time as the correct UTC instant", async () => {
            await openConflict(C.deferred);
            await click(screen.getByText("Defer"));
            await type(screen.getByTestId("revisit-at"), LOCAL);
            await type(screen.getByTestId("resolve-note"), NOTE);
            await click(screen.getByTestId("confirm-resolution"));

            await waitFor(() => {
                expect(sql(`SELECT status FROM memory_conflicts WHERE id = '${C.deferred}'`))
                    .toBe("deferred");
            }, { timeout: 10000 });

            // datetime-local carries no offset, so the page interprets it in
            // the local zone and converts. The stored instant must equal the
            // same conversion done here, not the literal wall-clock string.
            const expected = new Date(LOCAL).toISOString();
            const stored = sql(
                `SELECT to_char(revisit_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') ` +
                `FROM memory_conflicts WHERE id = '${C.deferred}'`
            );
            expect(stored).toBe(expected.replace(/\.\d{3}Z$/, "Z"));
        });

        test("the conflict is deferred, not resolved", async () => {
            expect(sql(`SELECT status FROM memory_conflicts WHERE id = '${C.deferred}'`))
                .toBe("deferred");
        });

        test("both memories stay current — deferring decides nothing", async () => {
            expect(sql(`SELECT current_version FROM memories WHERE id = '00000000-0000-4000-8000-00000000a003'`)).toBe("t");
            expect(sql(`SELECT current_version FROM memories WHERE id = '00000000-0000-4000-8000-00000000b003'`)).toBe("t");
        });

        /**
         * Was a recorded defect: the `deferred` branch returned before the
         * shared UPDATE, so the note the user typed was accepted, answered
         * 200, and dropped — along with resolver_id. Fixed by Codex in
         * `04babab`, merged here; D-010 in DECISIONS.md.
         *
         * Now asserts the corrected behaviour. Deliberately does NOT assert
         * through boolean rendering — see the note on `nullness` below.
         */
        test("the note and the deferring user are persisted", async () => {
            const [note, resolver] = row(
                `SELECT COALESCE(resolution_note, '<NULL>'), ` +
                `COALESCE(resolver_id::text, '<NULL>') ` +
                `FROM memory_conflicts WHERE id = '${C.deferred}'`
            );
            expect(note).toBe(NOTE);
            expect(resolver).toBe(DEV_USER);
        });

        test("resolved_at stays NULL — a deferral is not a resolution", async () => {
            // `SELECT resolved_at IS NOT NULL` would render 'f' here, and 'f'
            // does mean false. But a NULL boolean renders as the EMPTY STRING
            // under psql -At, not as 'f', so an assertion written against that
            // rendering conflates "false" with "unknown" the moment the
            // expression changes. An explicit sentinel cannot be misread.
            expect(sql(
                `SELECT CASE WHEN resolved_at IS NULL THEN 'IS_NULL' ELSE 'IS_SET' END ` +
                `FROM memory_conflicts WHERE id = '${C.deferred}'`
            )).toBe("IS_NULL");
        });

        test("blocks_derivation is unchanged — an open question still blocks", async () => {
            // Seeded false and must stay false. The point is that the deferred
            // path does not run the shared UPDATE's `blocks_derivation = FALSE`,
            // so this value is whatever it already was, not something the
            // resolution set.
            expect(sql(
                `SELECT CASE blocks_derivation WHEN TRUE THEN 'BLOCKS' ELSE 'DOES_NOT_BLOCK' END ` +
                `FROM memory_conflicts WHERE id = '${C.deferred}'`
            )).toBe("DOES_NOT_BLOCK");
        });

        test("revisit_at survives the fix — the new columns did not disturb it", async () => {
            const expected = new Date(LOCAL).toISOString().replace(/\.\d{3}Z$/, "Z");
            expect(sql(
                `SELECT to_char(revisit_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') ` +
                `FROM memory_conflicts WHERE id = '${C.deferred}'`
            )).toBe(expected);
        });

        /**
         * Guards every other assertion in this file that compares against 't'
         * or 'f'. psql renders a bare boolean as t/f, but `(TRUE)::text` as
         * 'true', and a NULL boolean as ''. If a future psql or a \pset change
         * altered that, assertions elsewhere would silently change meaning
         * rather than fail. This pins it.
         */
        test("psql boolean rendering is t / f / empty-for-null, as assumed", () => {
            expect(sql("SELECT TRUE, FALSE, NULL::boolean")).toBe("t|f|");
        });
    });

    // ── error presentation ────────────────────────────────────────────────
    describe("errors", () => {
        test("a missing required field is refused in the form, with no request sent", async () => {
            await openConflict(C.merged);
            await click(screen.getByText("Merge both"));

            const spy = jest.spyOn(global, "fetch");
            await click(screen.getByTestId("confirm-resolution"));

            expect(await screen.findByText(/merged content is required/i)).toBeTruthy();
            const posts = spy.mock.calls.filter(([, init]) => init?.method === "POST");
            expect(posts).toHaveLength(0);
        });

        test("a conflict that does not exist renders the neutral missing state", async () => {
            render(<Page id={C.missing} />);
            const state = await screen.findByTestId("error-state", {}, { timeout: 10000 });
            expect(state.getAttribute("data-error-kind")).toBe("missing");
            // 404 is what a non-member also receives. The copy must not
            // distinguish "no such conflict" from "not yours".
            expect(state.textContent).not.toMatch(/permission|forbidden|denied|exists/i);
        });
    });
});
