// Pure view-model helpers for a memory's source provenance (MemoryResponse.source,
// .authorship, .source_state). Wording rule: the account that connected or
// synced the source is never named as an author, and a shared authorship is
// never given a percentage.

/** The URL when it is a well-formed https URL, otherwise null (never linked). */
export function safeHttpsUrl(value) {
    if (typeof value !== "string" || value.length === 0) return null;
    try {
        const url = new URL(value);
        return url.protocol === "https:" ? url.href : null;
    } catch {
        return null;
    }
}

const TYPE_LABEL = {
    pull_request: "pull request",
    pull_review: "pull request review",
    pull_review_comment: "review comment",
    issue: "issue",
    issue_comment: "issue comment",
    commit: "commit",
};

export function sourceTypeLabel(source) {
    if (!source) return null;
    const tool = source.tool === "github" ? "GitHub" : source.tool || "source";
    const type = TYPE_LABEL[source.type] || (source.type ? String(source.type).replace(/_/g, " ") : null);
    return type ? `${tool} ${type}` : tool;
}

/** "revision 2" when the per-artifact counter is known, else a short digest, else null. */
export function revisionLabel(source) {
    if (!source) return null;
    if (Number.isInteger(source.revision_seq)) return `revision ${source.revision_seq}`;
    if (typeof source.revision === "string" && source.revision) return `revision ${source.revision.slice(0, 8)}`;
    return null;
}

/** Text for the authorship line, or null when nothing is known. */
export function authorshipLabel(authorship) {
    if (!authorship) return null;
    if (authorship.status === "shared") {
        const authors = Array.isArray(authorship.source_authors) ? authorship.source_authors.filter(Boolean) : [];
        return authors.length > 0 ? `Multiple authors: ${authors.join(", ")}` : "Multiple authors";
    }
    if (authorship.status === "unresolved") {
        return authorship.source_author
            ? `Unresolved author: ${authorship.source_author}`
            : "Unresolved author";
    }
    if (authorship.status === "resolved") {
        return authorship.source_author ? `Written by ${authorship.source_author}` : null;
    }
    return null;
}

const STALE_REASON = {
    source_revised: "The source was edited after this was imported.",
    source_deleted: "The source was deleted after this was imported.",
    connector_removed: "The connector this came from was removed, so it is no longer kept in step with its source.",
};

/** null when current/absent; otherwise what the notice must say. */
export function staleNotice(sourceState) {
    if (!sourceState || sourceState.status !== "stale") return null;
    return {
        text: STALE_REASON[sourceState.reason] || "The source changed since this was imported.",
        since: sourceState.since || null,
        newerUrl: safeHttpsUrl(sourceState.newer_source_url),
    };
}
