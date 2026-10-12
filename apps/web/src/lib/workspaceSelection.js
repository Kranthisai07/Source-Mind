/**
 * Which workspace the signed-in user last chose, remembered per user.
 *
 * Storage is a convenience, never a requirement: every access is wrapped so a
 * blocked or full localStorage degrades to "no remembered choice" and the
 * caller falls back to the first workspace in the fresh list.
 *
 * The remembered id is only ever a HINT. It is validated against the list the
 * API just returned, so an id for a workspace the user has left (or that was
 * written by a different account) can never be used to scope a request.
 */

const PREFIX = "sourcemind.workspace.";

const keyFor = (userId) => `${PREFIX}${userId}`;

export function readSelection(userId) {
    if (!userId) return null;
    try {
        return window.localStorage.getItem(keyFor(userId)) || null;
    } catch {
        return null;
    }
}

export function writeSelection(userId, workspaceId) {
    if (!userId || !workspaceId) return;
    try {
        window.localStorage.setItem(keyFor(userId), workspaceId);
    } catch {
        // Unavailable storage just means the choice is not remembered.
    }
}

export function clearSelection(userId) {
    if (!userId) return;
    try {
        window.localStorage.removeItem(keyFor(userId));
    } catch {
        // See writeSelection.
    }
}

/**
 * Pick the workspace id to use from a fresh, non-empty list.
 *
 * `preferred` is the in-memory choice for this session (checked first);
 * otherwise the persisted one. A candidate that is not in `list` is stale: it
 * is cleared and the first workspace is used instead.
 */
export function chooseWorkspaceId(list, userId, preferred = null) {
    const ids = new Set(list.map((w) => w.id));
    const candidate = preferred || readSelection(userId);
    if (candidate && ids.has(candidate)) return candidate;
    if (candidate) clearSelection(userId);
    return list[0].id;
}
