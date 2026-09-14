/**
 * Validation and payload construction for conflict resolution.
 *
 * Kept out of the component so the rules can be tested directly, without
 * rendering anything or pulling react-router into a unit test. Same reasoning
 * as apiError.js and requestGate.js.
 */

/**
 * Validate the chosen action's inputs against what resolve_conflict requires.
 * Mirrors the server's own checks so a predictable rejection is caught here
 * rather than surfacing as a 500 from a raised ValueError.
 */
export function validateResolution(action, fields) {
    const errors = {};
    if (!action) return { errors, ok: false };

    if (action === "merged" && !String(fields.mergedContent ?? "").trim()) {
        errors.mergedContent = "Merged content is required.";
    }
    if (action === "split") {
        if (!String(fields.tagA ?? "").trim()) errors.tagA = "Tag A is required.";
        if (!String(fields.tagB ?? "").trim()) errors.tagB = "Tag B is required.";
        if (
            String(fields.tagA ?? "").trim() &&
            String(fields.tagA ?? "").trim() === String(fields.tagB ?? "").trim()
        ) {
            errors.tagB = "The two tags must differ.";
        }
    }
    if (action === "deferred") {
        const raw = String(fields.revisitAt ?? "").trim();
        if (!raw) {
            errors.revisitAt = "A revisit date is required.";
        } else if (Number.isNaN(new Date(raw).getTime())) {
            errors.revisitAt = "That date could not be read.";
        } else if (new Date(raw).getTime() <= Date.now()) {
            errors.revisitAt = "Pick a time in the future.";
        }
    }
    return { errors, ok: Object.keys(errors).length === 0 };
}

/**
 * Build the request payload for the chosen action.
 *
 * revisit_at is converted to an ISO-8601 UTC instant. The input is
 * datetime-local, which yields a wall-clock string with no offset; passing it
 * through unchanged would let the server read the user's local time as UTC.
 * `new Date(local).toISOString()` interprets it in the browser's timezone and
 * emits an explicit instant, which is what TIMESTAMP(timezone=True) needs.
 */
export function buildResolutionPayload(action, fields) {
    // This builds the ARGUMENT for realApi.resolveConflict, which maps `note`
    // onto the wire key `resolution_note` (the field ResolveBody declares).
    // The wire mapping itself is pinned in apiContract.test.js.
    //
    // An untouched textarea yields "", which would be stored as an empty note
    // rather than no note. Normalised to null, matching `str | None = None`.
    const trimmedNote = String(fields.note ?? "").trim();
    const payload = {
        resolution_type: action,
        note: trimmedNote === "" ? null : trimmedNote,
    };
    if (action === "merged") payload.merged_content = String(fields.mergedContent).trim();
    if (action === "split") {
        payload.tag_a = String(fields.tagA).trim();
        payload.tag_b = String(fields.tagB).trim();
    }
    if (action === "deferred") {
        payload.revisit_at = new Date(fields.revisitAt).toISOString();
    }
    return payload;
}
