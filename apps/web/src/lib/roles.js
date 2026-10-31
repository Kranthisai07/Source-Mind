/**
 * Role names as people say them, and who may do what. Mirrors the server's
 * rules (services/workspace/roles.py, invitations.py) only to decide what to
 * OFFER; the server enforces every one of them regardless.
 */

export const ROLE_LABELS = {
    owner: "Founder",
    admin: "Admin",
    member: "Teammate",
    viewer: "Viewer",
};

export const ROLE_SUMMARIES = {
    owner: "Everything, including promoting admins and other founders.",
    admin: "Manages the team's data, connectors and teammates. Cannot promote anyone to admin or founder.",
    member: "Adds and reads the team's memory.",
    viewer: "Reads only.",
};

export const ALL_ROLES = ["owner", "admin", "member", "viewer"];

export const roleLabel = (role) => ROLE_LABELS[String(role || "").toLowerCase()] || String(role || "");

/** Roles the caller may invite someone as: founders any, admins teammate/viewer. */
export function invitableRoles(callerRole) {
    if (callerRole === "owner") return ALL_ROLES;
    if (callerRole === "admin") return ["member", "viewer"];
    return [];
}

/**
 * Roles the caller may move `targetRole` to. Admins only shuffle teammates and
 * viewers; founders may set anything. Never includes the current role.
 */
export function assignableRoles(callerRole, targetRole) {
    if (callerRole === "owner") return ALL_ROLES.filter((r) => r !== targetRole);
    if (callerRole === "admin" && (targetRole === "member" || targetRole === "viewer")) {
        return ["member", "viewer"].filter((r) => r !== targetRole);
    }
    return [];
}
