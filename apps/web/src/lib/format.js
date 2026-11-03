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
        case "HIGH":   return { bg: "#d03b3b20", text: "#d03b3b", border: "#d03b3b40" };
        case "MEDIUM": return { bg: "#fab21920", text: "#fab219", border: "#fab21940" };
        case "LOW":    return { bg: "#0ca30c20", text: "#0ca30c", border: "#0ca30c40" };
        default:       return { bg: "#7D847920", text: "#7D8479", border: "#7D847940" };
    }
}

export function statusColor(status) {
    const s = (status || "").toLowerCase();
    if (s === "active" || s === "resolved" || s === "success" || s === "done") return "#0ca30c";
    if (s === "syncing" || s === "running" || s === "under_review")             return "#2a78d6";
    if (s === "partial" || s === "initiated" || s === "open")                   return "#fab219";
    if (s === "error" || s === "failed")                                        return "#d03b3b";
    if (s === "deferred" || s === "pending")                                    return "#7D8479";
    if (s === "assigned")                                                       return "#6250d6";
    return "#7D8479";
}

export function severityColor(sev) {
    const s = (sev || "").toLowerCase();
    if (s === "critical" || s === "high") return "#d03b3b";
    if (s === "medium")                    return "#fab219";
    if (s === "low")                       return "#0ca30c";
    return "#7D8479";
}

export function tierColor(tier) {
    const t = (tier || "").toUpperCase();
    if (t === "CRITICAL")  return "#d03b3b";
    if (t === "IMPORTANT") return "#fab219";
    if (t === "STANDARD")  return "#2a78d6";
    return "#7D8479";
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
