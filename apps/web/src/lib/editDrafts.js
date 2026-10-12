/**
 * Unsaved memory-edit drafts, kept per (user, memory id).
 *
 * Component state is the primary home of a draft; sessionStorage only lets it
 * survive a remount or a navigation to the latest version. Every storage call
 * is wrapped: private mode, quota or a disabled store must never break the
 * page, so failures degrade to "no persistence".
 */

const PREFIX = "sm.editDraft.v1";
const CARRY_PREFIX = "sm.editDraftCarry.v1";

const scope = (userId) => userId || "anon";
const draftKey = (userId, memoryId) => `${PREFIX}:${scope(userId)}:${memoryId}`;
const carryKey = (userId, memoryId) => `${CARRY_PREFIX}:${scope(userId)}:${memoryId}`;

function read(key) {
    try {
        const raw = window.sessionStorage.getItem(key);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed.content === "string" ? parsed : null;
    } catch {
        return null;
    }
}

/** @returns {boolean} whether the value was actually stored */
function write(key, value) {
    try {
        window.sessionStorage.setItem(key, JSON.stringify(value));
        return true;
    } catch {
        // Persistence is best effort; the caller learns it failed.
        return false;
    }
}

function remove(key) {
    try {
        window.sessionStorage.removeItem(key);
    } catch {
        // Persistence is best effort.
    }
}

/** @returns {{content: string, tags: string}|null} */
export function loadDraft(userId, memoryId) {
    const draft = read(draftKey(userId, memoryId));
    return draft ? { content: draft.content, tags: String(draft.tags ?? "") } : null;
}

export function saveDraft(userId, memoryId, draft) {
    write(draftKey(userId, memoryId), { content: draft.content, tags: draft.tags });
}

export function clearDraft(userId, memoryId) {
    remove(draftKey(userId, memoryId));
}

/**
 * Hand a draft to another memory id (the latest version). The target page only
 * OFFERS it; nothing is opened or sent until the user chooses. Returns whether
 * storage accepted it; callers also pass the draft in router state so a storage
 * failure cannot lose it.
 */
export function carryDraft(userId, fromId, toId, draft) {
    return write(carryKey(userId, toId), { content: draft.content, tags: draft.tags, fromId });
}

/** @returns {{content: string, tags: string, fromId: string|null}|null} */
export function loadCarriedDraft(userId, memoryId) {
    const carried = read(carryKey(userId, memoryId));
    return carried
        ? { content: carried.content, tags: String(carried.tags ?? ""), fromId: carried.fromId ?? null }
        : null;
}

export function clearCarriedDraft(userId, memoryId) {
    remove(carryKey(userId, memoryId));
}
