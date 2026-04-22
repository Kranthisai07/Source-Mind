// Tiny utility helpers used across SourceMind.

export function relativeTime(iso) {
    const then = new Date(iso).getTime();
    const diff = Math.max(0, Date.now() - then);
    const sec = Math.floor(diff / 1000);
    if (sec < 60)  return `${sec}s ago`;
    const min = Math.floor(sec / 60);
    if (min < 60)  return `${min} min ago`;
    const hr = Math.floor(min / 60);
    if (hr  < 24)  return `${hr}h ago`;
    const d  = Math.floor(hr / 24);
    if (d   < 30)  return `${d}d ago`;
    const mo = Math.floor(d / 30);
    if (mo  < 12)  return `${mo}mo ago`;
    return `${Math.floor(mo / 12)}y ago`;
}

export function initials(nameOrLogin = "") {
    const parts = nameOrLogin.replace(/[_.]/g, " ").split(/\s+/).filter(Boolean);
    if (parts.length === 0) return "?";
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function riskColor(level) {
    switch ((level || "").toUpperCase()) {
        case "HIGH":   return { bg: "#EF444420", text: "#EF4444", border: "#EF444440" };
        case "MEDIUM": return { bg: "#F59E0B20", text: "#F59E0B", border: "#F59E0B40" };
        case "LOW":    return { bg: "#34D39920", text: "#34D399", border: "#34D39940" };
        default:       return { bg: "#8888A820", text: "#8888A8", border: "#8888A840" };
    }
}

export function statusColor(status) {
    const s = (status || "").toLowerCase();
    if (s === "active" || s === "resolved" || s === "success" || s === "done") return "#34D399";
    if (s === "syncing" || s === "running" || s === "under_review")             return "#4F7EFF";
    if (s === "partial" || s === "initiated" || s === "open")                   return "#F59E0B";
    if (s === "error" || s === "failed")                                        return "#EF4444";
    if (s === "deferred" || s === "pending")                                    return "#8888A8";
    if (s === "assigned")                                                       return "#A78BFA";
    return "#8888A8";
}

export function severityColor(sev) {
    const s = (sev || "").toLowerCase();
    if (s === "high")    return "#EF4444";
    if (s === "medium")  return "#F59E0B";
    if (s === "low")     return "#34D399";
    return "#8888A8";
}

export function tierColor(tier) {
    const t = (tier || "").toUpperCase();
    if (t === "CRITICAL")  return "#EF4444";
    if (t === "IMPORTANT") return "#F59E0B";
    if (t === "STANDARD")  return "#4F7EFF";
    return "#8888A8";
}

export function daysUntil(iso) {
    const diff = new Date(iso).getTime() - Date.now();
    return Math.ceil(diff / 86400000);
}

export function formatDate(iso) {
    try {
        return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
    } catch {
        return iso;
    }
}

export function stripMarkdown(s = "") {
    return s
        .replace(/```[\s\S]*?```/g, " ")        // fenced code blocks
        .replace(/`([^`]+)`/g, "$1")            // inline code
        .replace(/!\[[^\]]*\]\([^)]*\)/g, "")   // images
        .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1") // links
        .replace(/^#{1,6}\s+/gm, "")            // headings
        .replace(/^>\s?/gm, "")                 // blockquotes
        .replace(/^[-*+]\s+/gm, "")             // lists
        .replace(/^\d+\.\s+/gm, "")             // ordered lists
        .replace(/\*\*([^*]+)\*\*/g, "$1")      // bold
        .replace(/\*([^*]+)\*/g, "$1")          // italic
        .replace(/_([^_]+)_/g, "$1")            // italic alt
        .replace(/~~([^~]+)~~/g, "$1")          // strikethrough
        .replace(/\|/g, " ")                    // table pipes
        .replace(/\n{2,}/g, " — ")
        .replace(/\s+/g, " ")
        .trim();
}
