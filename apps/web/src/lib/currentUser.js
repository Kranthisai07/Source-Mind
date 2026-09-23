/**
 * The signed-in user id, published to plain modules.
 *
 * Same seam as authToken.js and for the same reason: realApi.js and the
 * submission-key holder are not components, so they cannot call useAuth().
 * A component inside <ClerkProvider> registers the id; everything else reads
 * it.
 *
 * Returns null when signed out, before the bridge has run, or in tests that
 * render a page without a provider — so callers must treat null as "unknown
 * user" rather than assuming a value.
 */

let _userId = null;

/** Called from inside the Clerk provider tree whenever the user changes. */
export function setCurrentUserId(id) {
    _userId = id ?? null;
}

export function getCurrentUserId() {
    return _userId;
}
