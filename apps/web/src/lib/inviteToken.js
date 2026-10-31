// Same shape the server accepts (schemas/invitation.py TOKEN_PATTERN).
export const INVITE_TOKEN_RE = /^smi_[A-Za-z0-9_-]{43}$/;

/**
 * Where sign-in may send the person afterwards. Only an invitation page is
 * allowed: a free-form `next` would be an open redirect.
 */
export function safeNextPath(next) {
    const value = String(next || "");
    const match = /^\/invite\/(smi_[A-Za-z0-9_-]{43})$/.exec(value);
    return match ? value : null;
}
