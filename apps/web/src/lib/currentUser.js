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
const _listeners = new Set();

/** Called from inside the Clerk provider tree whenever the user changes. */
export function setCurrentUserId(id) {
    const next = id ?? null;
    if (next === _userId) return;
    _userId = next;
    // Copy first: a listener may unsubscribe while being told.
    for (const listener of Array.from(_listeners)) listener();
}

/**
 * Subscribe to the id being published or changed. The bridge publishes from an
 * effect AFTER first render, so anything that keyed state on the id at mount
 * (a per-user draft, say) must hear about the settled value.
 */
export function onCurrentUserChange(listener) {
    _listeners.add(listener);
    return () => { _listeners.delete(listener); };
}

export function getCurrentUserId() {
    return _userId;
}
