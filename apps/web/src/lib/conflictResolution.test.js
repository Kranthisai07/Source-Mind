/**
 * Payload and validation rules for the five conflict resolutions.
 *
 * resolve_conflict raises ValueError when a required field is absent —
 * merged_content for 'merged', tag_a/tag_b for 'split', revisit_at for
 * 'deferred' — and that surfaces as a 500. These mirror the server's own
 * checks so a predictable rejection is caught in the form instead.
 *
 * The timezone case is the subtle one. `<input type="datetime-local">` yields a
 * wall-clock string with no offset, while memory_conflicts.revisit_at is
 * TIMESTAMP(timezone=True) and resolver.py binds the parsed datetime straight
 * to asyncpg. Sending the raw local string would store the user's wall clock as
 * though it were UTC, and the conflict would resurface at the wrong hour.
 */

import { validateResolution, buildResolutionPayload } from "./conflictResolution";

const base = {
    note: "", mergedContent: "", tagA: "", tagB: "", revisitAt: "",
};

describe("actions needing no extra input", () => {
    test.each(["kept_a", "kept_b"])("%s validates with nothing else", (action) => {
        expect(validateResolution(action, base).ok).toBe(true);
    });

    test("kept_a sends only the type and the note", () => {
        const p = buildResolutionPayload("kept_a", { ...base, note: "A is current" });
        expect(p).toEqual({ resolution_type: "kept_a", note: "A is current" });
        // Absent rather than null: a field the action does not use is not sent.
        expect(p).not.toHaveProperty("merged_content");
        expect(p).not.toHaveProperty("revisit_at");
    });
});

describe("merged requires merged_content", () => {
    test("empty content is rejected", () => {
        const { ok, errors } = validateResolution("merged", base);
        expect(ok).toBe(false);
        expect(errors.mergedContent).toBeTruthy();
    });

    test("whitespace-only content is rejected", () => {
        expect(validateResolution("merged", { ...base, mergedContent: "   \n " }).ok).toBe(false);
    });

    test("valid content is trimmed and sent as merged_content", () => {
        const p = buildResolutionPayload("merged", {
            ...base, mergedContent: "  HNSW is the production index.  ", note: "combined",
        });
        expect(p.merged_content).toBe("HNSW is the production index.");
        expect(p.note).toBe("combined");
        expect(p.resolution_type).toBe("merged");
    });
});

describe("split requires two distinct tags", () => {
    test("both missing", () => {
        const { ok, errors } = validateResolution("split", base);
        expect(ok).toBe(false);
        expect(errors.tagA).toBeTruthy();
        expect(errors.tagB).toBeTruthy();
    });

    test("one missing", () => {
        const { errors } = validateResolution("split", { ...base, tagA: "pg16" });
        expect(errors.tagA).toBeUndefined();
        expect(errors.tagB).toBeTruthy();
    });

    test("identical tags are rejected — splitting into one bucket is not a split", () => {
        const { ok, errors } = validateResolution("split", { ...base, tagA: "pg16", tagB: "pg16" });
        expect(ok).toBe(false);
        expect(errors.tagB).toMatch(/differ/i);
    });

    test("valid tags are trimmed and sent", () => {
        const p = buildResolutionPayload("split", { ...base, tagA: " pg16 ", tagB: " pg18 " });
        expect(p.tag_a).toBe("pg16");
        expect(p.tag_b).toBe("pg18");
    });
});

describe("deferred requires a future revisit_at with an explicit offset", () => {
    test("missing date is rejected", () => {
        expect(validateResolution("deferred", base).errors.revisitAt).toBeTruthy();
    });

    test("an unparseable date is rejected", () => {
        expect(
            validateResolution("deferred", { ...base, revisitAt: "not-a-date" }).errors.revisitAt
        ).toBeTruthy();
    });

    test("a past date is rejected — deferring to the past never resurfaces", () => {
        const past = new Date(Date.now() - 86_400_000).toISOString().slice(0, 16);
        expect(validateResolution("deferred", { ...base, revisitAt: past }).ok).toBe(false);
    });

    test("a future date validates", () => {
        const future = new Date(Date.now() + 86_400_000).toISOString().slice(0, 16);
        expect(validateResolution("deferred", { ...base, revisitAt: future }).ok).toBe(true);
    });

    test("the local wall clock is converted to a UTC instant, not sent raw", () => {
        // What datetime-local produces: no offset, no Z.
        const local = "2030-06-01T09:30";
        const p = buildResolutionPayload("deferred", { ...base, revisitAt: local });

        // Must not be passed through unchanged — that is how a local time gets
        // stored as UTC against a TIMESTAMP(timezone=True) column.
        expect(p.revisit_at).not.toBe(local);
        expect(p.revisit_at).toMatch(/Z$/);

        // It denotes the same instant the user picked in their own timezone.
        expect(new Date(p.revisit_at).getTime()).toBe(new Date(local).getTime());
    });

    test("the emitted value round-trips as a valid ISO-8601 instant", () => {
        const future = new Date(Date.now() + 3_600_000).toISOString().slice(0, 16);
        const p = buildResolutionPayload("deferred", { ...base, revisitAt: future });
        expect(Number.isNaN(Date.parse(p.revisit_at))).toBe(false);
    });
});

describe("the note survives every action", () => {
    test.each(["kept_a", "kept_b", "merged", "split", "deferred"])(
        "%s carries resolution_note",
        (action) => {
            const p = buildResolutionPayload(action, {
                note: "why this call was made",
                mergedContent: "merged text",
                tagA: "a", tagB: "b",
                revisitAt: "2030-01-01T00:00",
            });
            // `note` is the adapter argument; realApi maps it to the wire
            // key resolution_note, which apiContract.test.js pins.
            expect(p.note).toBe("why this call was made");
        }
    );

    test("an empty note is sent as null, matching `str | None = None`", () => {
        expect(buildResolutionPayload("kept_a", base).note).toBeNull();
    });
});

describe("mark_outdated is not offered", () => {
    test("it is not a buildable action", () => {
        // kept_a / kept_b already set current_version = FALSE on the losing
        // memory, so 'mark one outdated' would be a second label for an action
        // that exists. buildResolutionPayload would happily pass the string
        // through, which is exactly why the UI must never offer it — the
        // server answers "Unknown resolution_type" with a 500.
        const p = buildResolutionPayload("mark_outdated", base);
        expect(p.resolution_type).toBe("mark_outdated");
        expect(["kept_a", "kept_b", "merged", "split", "deferred"]).not.toContain(
            p.resolution_type
        );
    });
});
