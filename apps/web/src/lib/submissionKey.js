/**
 * Idempotency keys bound to what is actually being submitted.
 *
 * The rule the Idempotency-Key header exists for has two halves, and an
 * earlier version of this only implemented one:
 *
 *   - a RETRY of the same submission must reuse its key, or the backend sees
 *     two unrelated ingestions of the same content;
 *   - a DIFFERENT submission must get a new key, or the backend sees a replay
 *     and may return the first job while silently discarding the new content.
 *
 * The second is the dangerous half. A user whose submit failed, who then edits
 * the text and submits again, would otherwise send the new text under the old
 * key and be told it succeeded — having ingested something else.
 *
 * So the key is keyed to an identity covering everything that makes a
 * submission distinct: the outgoing payload, the RESOLVED workspace, and the
 * authenticated user. Change any of them and the key changes with it.
 */

import { newIdempotencyKey } from "./idempotency";

/**
 * A stable fingerprint of a submission.
 *
 * JSON.stringify of a literal, not of arbitrary object key order: the payload
 * is constructed here in a fixed shape so two equal submissions always produce
 * the same string.
 */
export function submissionIdentity({ payload, workspaceId, userId }) {
    return JSON.stringify({
        payload: payload ?? null,
        workspaceId: workspaceId ?? null,
        // null means "unknown user" and is deliberately part of the identity:
        // if the bridge wires a real id later, that is a change and earns a
        // new key rather than silently reusing one minted while signed out.
        userId: userId ?? null,
    });
}

/**
 * Remembers one key for one submission identity.
 *
 * `keyFor(identity)` returns the held key when the identity is unchanged, and
 * mints a new one the moment anything differs.
 */
export function createSubmissionKeyHolder(mint = newIdempotencyKey) {
    let heldIdentity = null;
    let heldKey = null;

    return {
        keyFor(identity) {
            if (identity !== heldIdentity || heldKey === null) {
                heldIdentity = identity;
                heldKey = mint();
            }
            return heldKey;
        },
        /** Called once a submission is accepted, so the next one is new. */
        clear() {
            heldIdentity = null;
            heldKey = null;
        },
        get current() {
            return heldKey;
        },
    };
}
