// Plain-language wording for the connector screens. Pure data and functions, so
// every branch is testable without rendering. Wording rule: say what is known.
// A denial ("access_lost") and an unverifiable state ("authorization_unverified")
// are different facts and must never read alike.

export const STATE_LABEL = {
    idle: "idle",
    syncing: "syncing",
    needs_attention: "needs attention",
    paused: "paused",
    paused_budget: "paused (budget)",
    error: "error",
    disconnected: "disconnected",
};

export const SCOPE_STATUS_TEXT = {
    ok: "Working. Access to this repository is confirmed.",
    authorization_unverified:
        "We could not confirm your GitHub access, so nothing is imported from this repository until we can. This is not a denial; try again later or re-scan.",
    reauthorization_required:
        "GitHub needs you to authorize SourceMind again. Disconnect and connect GitHub again to continue importing.",
    access_lost:
        "GitHub reports that access to this repository is gone (removed, renamed or no longer shared with the app). Nothing new is imported from it.",
    error: "Importing from this repository failed. See the error code and try Sync now or Re-scan.",
};

export const SCOPE_STATUS_LABEL = {
    ok: "ok",
    authorization_unverified: "access not confirmed",
    reauthorization_required: "authorize again",
    access_lost: "access lost",
    error: "error",
};

export const ARTIFACT_LABEL = {
    commits: "commits",
    pull_requests: "pull requests",
    issues: "issues",
    issue_comments: "issue comments",
    review_comments: "review comments",
    review_reconcile: "review reconcile",
};

export const FILTER_LABEL = {
    skip_bots: "bot-authored",
    skip_empty_body: "empty body",
};

// error_code on a sync log or connector. The backend's own error text is
// deliberately generic, so this mapping is the only explanation shown.
export const ERROR_CODE_TEXT = {
    access_lost: "GitHub no longer shows access to a repository.",
    reauthorization_required: "GitHub needs you to authorize SourceMind again.",
    authorization_unverified: "We could not confirm your GitHub access, so nothing was imported.",
    rate_limited: "GitHub rate limit reached. The sync will continue later.",
    permission_denied: "GitHub denied the request (missing permission).",
    verifier_unavailable: "We could not reach GitHub to confirm access, so nothing was imported.",
    github_unavailable: "GitHub could not be reached.",
    github_bad_response: "GitHub sent a response we could not read.",
    github_graphql_errors: "GitHub returned errors for part of the request.",
    github_denied: "GitHub denied the authorization.",
    lease_lost: "Another sync took over this run; it will be retried.",
    handoff_failed: "Some items could not be handed to processing and will be retried.",
};

export function describeErrorCode(code) {
    if (!code) return null;
    return ERROR_CODE_TEXT[code] || `The sync failed (code: ${code}).`;
}

// Reasons carried in error.details.reason by the connect flow.
const CONNECT_REASON_TEXT = {
    connect_state_invalid:
        "This GitHub authorization expired, was already used, or does not belong to your session. Start again.",
    connect_ticket_invalid:
        "The GitHub authorization expired or was already used. Connect again.",
    github_denied: "GitHub denied the authorization. Nothing was connected.",
    code_rejected: "GitHub rejected the authorization code (it may have expired). Start again.",
    no_installation:
        "The SourceMind GitHub App is not installed on any account you can reach. Install it on GitHub, then connect again.",
    installation_required:
        "The GitHub App is installed in more than one place. Start again and choose the installation to use.",
    installation_not_reachable:
        "You cannot reach that GitHub installation. Choose one you have access to.",
    github_unavailable: "GitHub could not be reached. Nothing was connected; try again shortly.",
    github_bad_response: "GitHub sent an unexpected response. Nothing was connected; try again shortly.",
    not_configured: "GitHub connection is not configured on this server.",
    repository_not_authorized:
        "One or more selected repositories are not covered by your GitHub authorization. Start again and pick from the list.",
    connector_exists: "A connector with this name already exists. Choose another name.",
    repository_already_connected:
        "Another connector in this workspace already imports one of these repositories. Disconnect it first, or pick different repositories.",
    connector_disconnected: "This connector is disconnected.",
    connector_paused: "The connector was paused while this was queued. Resume it, then use Retry failed.",
    connector_paused: "This connector is paused. Resume it first.",
    authorization_missing: "This connector must be connected to GitHub again.",
};

/**
 * Friendly, honest message for a failed connector request. Never implies
 * success. `fallback` is used when the failure is not classified.
 */
export function describeConnectorError(error, fallback = "The request failed.") {
    const status = typeof error?.status === "number" ? error.status : null;
    const details = error?.body?.error?.details;
    const reason = details && typeof details.reason === "string" ? details.reason : null;
    if (error?.network || status === 0) {
        return "Could not reach SourceMind. Check your connection; nothing was changed.";
    }
    if (reason && CONNECT_REASON_TEXT[reason]) return CONNECT_REASON_TEXT[reason];
    if (status === 401) return "Your session has expired. Sign in again.";
    if (status === 403) return "Only a workspace owner or admin can manage connectors.";
    if (status === 404) return "This connector is not available. It may have been removed.";
    if (status === 422) return "The request was not accepted as entered. Check the values and try again.";
    if (status === 429) {
        const wait = typeof error?.retryAfterSeconds === "number" ? ` Try again in ${error.retryAfterSeconds}s.` : " Wait a moment and try again.";
        return `Too many requests.${wait}`;
    }
    if (status !== null && status >= 500) return "The server could not complete this request. Try again shortly.";
    return fallback;
}

export function formatCount(value) {
    return Number.isFinite(value) ? value.toLocaleString() : "—";
}

export function durationLabel(startedAt, completedAt) {
    const start = Date.parse(startedAt);
    const end = Date.parse(completedAt);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "—";
    const sec = Math.round((end - start) / 1000);
    if (sec < 60) return `${sec}s`;
    return `${Math.floor(sec / 60)}m ${sec % 60}s`;
}

// Browser hand-off to GitHub. Kept here so tests can replace it; jsdom cannot
// navigate. Only an https GitHub URL (or this app's own origin, which is how the
// mock client stands in for GitHub) is ever followed.
export function isGithubAuthorizeUrl(value) {
    try {
        const url = new URL(String(value));
        if (typeof window !== "undefined" && url.origin === window.location.origin) return true;
        return url.protocol === "https:" && (url.hostname === "github.com" || url.hostname.endsWith(".github.com"));
    } catch {
        return false;
    }
}

export function redirectTo(url) {
    window.location.assign(url);
}

// The authorization code is single-use and sensitive. It lives only in this
// module variable between the callback route and the page that redeems it:
// never in the URL after the route runs, never in storage, never logged.
let pendingCallback = null;
export function stashGithubCallback(params) {
    pendingCallback = params;
}
export function takeGithubCallback() {
    const taken = pendingCallback;
    pendingCallback = null;
    return taken;
}
