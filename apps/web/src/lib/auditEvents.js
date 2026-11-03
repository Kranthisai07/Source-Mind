import { roleLabel } from "./roles";

/** Day heading for a group of events: Today, Yesterday, or the date. */
export function dayLabel(iso, now = new Date()) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "Earlier";
    const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const days = Math.round((start(now) - start(d)) / 86400000);
    if (days === 0) return "Today";
    if (days === 1) return "Yesterday";
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

const who = (name, id) => name || (id ? "A former member" : "Someone");

/** One plain sentence per audit event; mirrors the server's event names (D-067). */
export function describeAuditEvent(e) {
    const actor = who(e.actor_name, e.actor_user_id);
    const target = e.target_name || e.target_email || (e.target_user_id ? "a former member" : "someone");
    switch (e.event) {
        case "member_added": return `${actor} added ${target} as ${roleLabel(e.role_after)}`;
        case "member_removed": return `${actor} removed ${target}`;
        case "role_changed": return `${actor} changed ${target} from ${roleLabel(e.role_before)} to ${roleLabel(e.role_after)}`;
        case "invitation_created": return `${actor} invited ${target} as ${roleLabel(e.role_after)}`;
        case "invitation_revoked": return `${actor} withdrew the invitation for ${target}`;
        case "invitation_accepted": return `${actor} accepted an invitation`;
        case "workspace_exported": return `${actor} exported the workspace data`;
        default: return `${actor}: ${String(e.event || "unknown event")}`;
    }
}
