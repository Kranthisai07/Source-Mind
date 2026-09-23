/**
 * Turn a rejected API request into something a page can render.
 *
 * Before this existed, every page did `.catch(() => setRows([]))`. That collapses
 * five different conditions into one screen:
 *
 *   401 the session expired          -> "No conflicts yet"
 *   403 your role cannot do this     -> "No conflicts yet"
 *   404 not yours / does not exist   -> "No conflicts yet"
 *   429 you are being rate limited   -> "No conflicts yet"
 *   5xx / offline                    -> "No conflicts yet"
 *
 * All five read as "everything is fine, there is simply no data" — the same
 * class of failure D-008 records, where a screen looks correct precisely
 * because it is broken. The stricter backend makes it far more likely: a
 * non-member now receives 404 by design, and an under-privileged member 403.
 *
 * Deliberately a pure function with no React and no imports, so every branch is
 * testable without rendering anything.
 */

/** @typedef {"auth"|"forbidden"|"missing"|"rate-limited"|"server"|"network"|"unknown"} ErrorKind */

/**
 * @param {unknown} error
 * @returns {{
 *   kind: ErrorKind, status: number|null, code: string|null,
 *   title: string, detail: string,
 *   retryable: boolean, retryAfterSeconds: number|null,
 * }}
 */
export function classifyApiError(error) {
    const status = typeof error?.status === "number" ? error.status : null;
    // The backend's structured error body: {error: {code, message, ...}}.
    const code = error?.body?.error?.code ?? null;
    const retryAfterSeconds =
        typeof error?.retryAfterSeconds === "number" ? error.retryAfterSeconds : null;

    // Transport failure. Distinct from 5xx: nothing was reached, so the request
    // may simply be repeatable once connectivity returns.
    if (error?.network || status === 0) {
        return {
            kind: "network",
            status: null,
            code,
            title: "Can't reach SourceMind",
            detail: "The request didn't get through. Check your connection and try again.",
            retryable: true,
            retryAfterSeconds: null,
        };
    }

    if (status === 401) {
        return {
            kind: "auth",
            status,
            code,
            title: "Your session has expired",
            detail: "Sign in again to continue.",
            // Retrying with the same dead token just repeats the 401.
            retryable: false,
            retryAfterSeconds: null,
        };
    }

    if (status === 403) {
        return {
            kind: "forbidden",
            status,
            code,
            title: "You don't have access to this",
            detail:
                "Your role in this workspace doesn't permit this action. " +
                "An owner or admin can change it.",
            retryable: false,
            retryAfterSeconds: null,
        };
    }

    if (status === 404) {
        // Deliberately neutral. The API answers 404 both for "no such record"
        // and for "exists but is not yours", precisely so the two cannot be
        // told apart. Wording that hinted a hidden resource existed would undo
        // that at the last step.
        return {
            kind: "missing",
            status,
            code,
            title: "Not available",
            detail: "This isn't available. It may have been removed, or you may no longer have access.",
            retryable: false,
            retryAfterSeconds: null,
        };
    }

    if (status === 429) {
        return {
            kind: "rate-limited",
            status,
            code,
            title: "Too many requests",
            detail: retryAfterSeconds
                ? `Rate limit reached. Retrying in ${retryAfterSeconds}s.`
                : "Rate limit reached. Wait a moment and try again.",
            retryable: true,
            retryAfterSeconds,
        };
    }

    if (status !== null && status >= 500) {
        return {
            kind: "server",
            status,
            code,
            title: "Something went wrong",
            detail:
                "The server couldn't complete this request. It may be temporary — try again.",
            retryable: true,
            retryAfterSeconds: null,
        };
    }

    return {
        kind: "unknown",
        status,
        code,
        title: "Something went wrong",
        detail: error?.message || "The request failed for an unknown reason.",
        retryable: true,
        retryAfterSeconds: null,
    };
}

/**
 * Losing access mid-session must clear what is already on screen; a stale list
 * rendered under a permission error is worse than an empty one.
 */
export function shouldClearData(kind) {
    return kind === "auth" || kind === "forbidden" || kind === "missing";
}

/** Backoff for an automatic retry, honouring the server's own number first. */
export function retryDelayMs(classified, attempt) {
    if (classified.retryAfterSeconds != null) {
        return classified.retryAfterSeconds * 1000;
    }
    // 1s, 2s, 4s — capped. Bounded on purpose: the limiter is fail-closed, so
    // unbounded client retries make an overloaded backend worse.
    return Math.min(1000 * 2 ** Math.max(0, attempt), 8000);
}
