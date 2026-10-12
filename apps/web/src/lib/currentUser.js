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
let _published = false;
const _listeners = new Set();

// Unsaved edit drafts live in sessionStorage under
// `sm.editDraft.v1:<user>:<memory>` and `sm.editDraftCarry.v1:<user>:<memory>`.
// They must not outlive the person who typed them.
const DRAFT_KEY_PREFIX = "sm.editDraft";

/** Remove every stored edit draft that does not belong to `keepUserId`. */
export function purgeEditDrafts(keepUserId = null) {
    try {
        const store = window.sessionStorage;
        const doomed = [];
        for (let i = 0; i < store.length; i += 1) {
            const key = store.key(i);
            if (key && key.startsWith(DRAFT_KEY_PREFIX) && key.split(":")[1] !== keepUserId) {
                doomed.push(key);
            }
        }
        doomed.forEach((key) => store.removeItem(key));
    } catch {
        // Storage may be unavailable; then there is nothing to purge.
    }
}

/** Called from inside the Clerk provider tree whenever the user changes. */
export function setCurrentUserId(id) {
    const next = id ?? null;
    const first = !_published;
    _published = true;
    // First publish of a page load, sign-out, or a different user: drop any
    // draft that is not the new user's (everything, when signed out).
    if (first || next !== _userId) purgeEditDrafts(next);
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
