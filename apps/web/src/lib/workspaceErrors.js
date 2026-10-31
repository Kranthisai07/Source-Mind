/**
 * Plain-language messages for the three workspace endpoints
 * (list, create, add member), keyed on the backend's `error.code` and
 * `error.details.reason` rather than on message text.
 */

const body = (err) => err?.body?.error || {};

export function mapWorkspaceError(err, action) {
    const e = body(err);
    const code = e.code || null;
    const reason = e.details?.reason || null;
    const status = err?.status ?? null;

    if (err?.network || status === 0) {
        return { kind: "network", message: "Could not reach SourceMind, so the result is unconfirmed. Check your connection, then check the list before trying again." };
    }
    if (status === 429 || code === "SM015") {
        const wait = err?.retryAfterSeconds;
        return {
            kind: "rate_limit",
            message: wait
                ? `Too many attempts. Try again in about ${wait} seconds.`
                : "Too many attempts. Please wait a while and try again.",
        };
    }

    if (action === "create") {
        if (code === "SM031") {
            return { kind: "slug_taken", field: "slug", message: "That URL name is already taken. Choose a different one." };
        }
        if (code === "SM010" || status === 422) {
            return { kind: "validation", message: e.message || "Check the workspace name and URL name, then try again." };
        }
    }

    if (action === "addMember") {
        // A route that does not exist answers 405, or 404 with no SourceMind
        // error code. Real "not found" answers carry a code (SM022, SM023).
        if (status === 405 || (status === 404 && !code)) {
            return { kind: "unsupported", message: "This server does not support adding members yet. Nobody was added." };
        }
        if (reason === "user_not_found" || code === "SM023") {
            return { kind: "user_not_found", message: "No account with that email yet. They need to sign in to SourceMind once first, then you can add them." };
        }
        if (reason === "email_not_verified") {
            return { kind: "email_not_verified", message: "That email address is not verified on their account, so they cannot be added." };
        }
        if (reason === "email_ambiguous") {
            return { kind: "email_ambiguous", message: "More than one account matches that email. Ask the person which account to use, or contact support." };
        }
        if (reason === "already_member") {
            return { kind: "already_member", message: "That person is already a member of this workspace." };
        }
        if (reason === "handoff_in_progress") {
            return { kind: "handoff_in_progress", message: "A handoff is in progress for that person. Finish it before adding them again." };
        }
        if (code === "SM005" || status === 403) {
            return { kind: "forbidden", message: "Only workspace owners and admins can add members." };
        }
        if (code === "SM055" || status === 503) {
            return { kind: "unavailable", message: "Could not verify the email with the sign-in provider right now. Nobody was added; try again shortly." };
        }
        if (code === "SM010" || status === 422) {
            return { kind: "validation", message: e.message || "Check the email address and role, then try again." };
        }
    }

    if (action === "invite" || action === "revokeInvite" || action === "changeRole" || action === "acceptInvite") {
        if (status === 405 || (status === 404 && !code && action !== "acceptInvite")) {
            return { kind: "unsupported", message: "This server does not support that yet. Nothing was changed." };
        }
        if (action === "acceptInvite" && (code === "SM056" || status === 404)) {
            // One answer for every reason on purpose: the server does not say which.
            return { kind: "invitation_unusable", message: "This invitation can't be used. It may have expired, been withdrawn, or been sent to a different email address. Ask the person who invited you for a new link." };
        }
        if (action === "invite" && reason === "already_member") {
            return { kind: "already_member", message: "That person is already a member of this workspace." };
        }
        if (code === "SM005" || status === 403) {
            // The server's sentence says exactly which rule applied (founder only, last founder...).
            return { kind: "forbidden", message: e.message || "You do not have permission to do that." };
        }
        if (code === "SM055" || status === 503) {
            return { kind: "unavailable", message: "Could not verify the email with the sign-in provider right now. Nothing was changed; try again shortly." };
        }
        if (code === "SM010" || status === 422) {
            return { kind: "validation", message: e.message || "Check the details and try again." };
        }
        if (action === "revokeInvite" && status === 404) {
            return { kind: "gone", message: "That invitation no longer exists. Refresh the list." };
        }
    }

    if (action === "export" || action === "audit") {
        if (status === 413 || code === "SM012") {
            return { kind: "too_large", message: "This workspace is too large to export in one file. Nothing was downloaded." };
        }
        if (code === "SM005" || status === 403) {
            return { kind: "forbidden", message: "Only founders and admins can do that." };
        }
        if (status === 405 || (status === 404 && !code)) {
            return { kind: "unsupported", message: "This server does not support that yet." };
        }
        return { kind: "generic", message: action === "export" ? "The export failed and nothing was downloaded. Try again." : "Could not load the activity log. Try again." };
    }

    return { kind: "generic", message: "Something went wrong and the result is unconfirmed. Check the list before trying again." };
}
