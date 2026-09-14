/**
 * Reject responses that arrive after the request they belong to stopped being
 * the current one.
 *
 * The concrete hazard: a page loads workspace A, the workspace changes to B, B
 * resolves quickly, then A's slower response lands and overwrites the screen
 * with data from a workspace the user is no longer in. The same shape occurs
 * when access is revoked mid-flight — an in-flight authorized read completes
 * and repopulates a screen that should now be empty.
 *
 * Pages previously used a `let cancelled` flag in an effect. That works for
 * unmount, but not for "the fourth request superseded the second": both are
 * still in the same effect instance, and neither is cancelled.
 *
 * Kept free of React so the ordering rules can be tested directly rather than
 * inferred from rendered output.
 */

export function createRequestGate() {
    let current = 0;
    let closed = false;

    return {
        /** Start a request; the token identifies it. Supersedes any in flight. */
        begin() {
            current += 1;
            return current;
        },

        /** True only for the most recent request, and only while open. */
        accept(token) {
            return !closed && token === current;
        },

        /**
         * Invalidate everything in flight without starting a new request.
         * Used when the workspace changes or access is lost, so a response
         * that is already on the wire cannot repopulate the screen.
         */
        invalidate() {
            current += 1;
        },

        /** Permanently close, e.g. on unmount. No token is accepted after this. */
        close() {
            closed = true;
        },

        /** Introspection for tests. */
        get token() {
            return current;
        },
        get isClosed() {
            return closed;
        },
    };
}
