/**
 * Turns the setup wizard's three questions into a list of invitations.
 * Pure, so the rules about duplicates and precedence are testable alone.
 */

const EMAIL_RE = /^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$/;
// Highest authority wins when one address is listed in several places.
const PRECEDENCE = ["owner", "admin", "member"];

export const MAX_SETUP_INVITES = 20;

/** Split on commas, semicolons, spaces and newlines; lowercase; drop repeats. */
export function parseEmails(text) {
    const seen = new Set();
    const valid = [];
    const invalid = [];
    for (const raw of String(text || "").split(/[\s,;]+/)) {
        const email = raw.trim().toLowerCase();
        if (!email || seen.has(email)) continue;
        seen.add(email);
        (EMAIL_RE.test(email) && email.length <= 254 ? valid : invalid).push(email);
    }
    return { valid, invalid };
}

/**
 * @param {{admin: string, founders: string, teammates: string}} input
 * @returns {{invites: {email: string, role: string}[], invalid: string[], tooMany: boolean}}
 */
export function buildInvitePlan({ admin = "", founders = "", teammates = "" }) {
    const byEmail = new Map();
    const invalid = [];
    const add = (text, role) => {
        const parsed = parseEmails(text);
        invalid.push(...parsed.invalid);
        for (const email of parsed.valid) {
            const current = byEmail.get(email);
            if (!current || PRECEDENCE.indexOf(role) < PRECEDENCE.indexOf(current)) byEmail.set(email, role);
        }
    };
    add(founders, "owner");
    add(admin, "admin");
    add(teammates, "member");
    const invites = [...byEmail].map(([email, role]) => ({ email, role }));
    return { invites, invalid, tooMany: invites.length > MAX_SETUP_INVITES };
}
