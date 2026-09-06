// Bridge between Clerk's React context and the plain-module API client.
//
// realApi.js is not a component, so it cannot call useAuth(). Clerk's
// getToken() is only reachable from inside the provider tree. This module is
// the seam: a component inside <ClerkProvider> registers the getter once, and
// the API client reads it per request.
//
// Registering the getter rather than caching a token is deliberate. Clerk
// session tokens are short-lived (~60s) and getToken() refreshes them
// transparently, so anything we cached here would be stale within a minute.

let _getToken = null;

/** Called once from inside the Clerk provider tree. */
export function setTokenGetter(fn) {
    _getToken = typeof fn === "function" ? fn : null;
}

export function hasTokenGetter() {
    return _getToken !== null;
}

/**
 * Current Clerk session JWT, or null when signed out / not yet wired.
 *
 * Never throws: a failure here must surface as a 401 from the API, which the
 * UI already handles, rather than as an unhandled rejection mid-render.
 */
export async function getAuthToken() {
    if (!_getToken) return null;
    try {
        return (await _getToken()) || null;
    } catch {
        return null;
    }
}

/**
 * One-line, permanently redacted proof that a real JWT is being attached.
 *
 * Logged once per session in development only. It prints the algorithm, the
 * issuer and subject claims, and the token's length — enough to confirm a
 * genuine Clerk JWT is flowing, while never writing the signature or the full
 * credential to the console. Redaction is by construction, not something to
 * remember to remove later.
 */
let _fingerprinted = false;
export function logTokenFingerprintOnce(token) {
    if (_fingerprinted || !token || process.env.NODE_ENV === "production") return;
    _fingerprinted = true;
    try {
        const [rawHeader, rawPayload] = token.split(".");
        const decode = (s) =>
            JSON.parse(atob(s.replace(/-/g, "+").replace(/_/g, "/")));
        const header = decode(rawHeader);
        const payload = decode(rawPayload);
        // eslint-disable-next-line no-console
        console.info(
            "[SourceMind] Clerk JWT attached ·",
            `alg=${header.alg}`,
            `iss=${payload.iss}`,
            `sub=${payload.sub}`,
            `exp=${new Date(payload.exp * 1000).toISOString()}`,
            `len=${token.length}`,
            "· signature and full token deliberately not logged"
        );
    } catch {
        // eslint-disable-next-line no-console
        console.info("[SourceMind] Bearer token attached (unparseable as JWT)");
    }
}
