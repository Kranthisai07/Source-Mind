import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertTriangle } from "lucide-react";
import TopBar from "../components/layout/TopBar";
import StatusBadge from "../components/widgets/StatusBadge";
import { PageLoadError } from "../components/widgets/PageLoadState";
import useApiResource from "../hooks/useApiResource";
import api from "../lib/api";
import { onIdentityReset } from "../lib/realApi";
import { relativeTime, severityColor } from "../lib/format";

const STATUS_TABS = [
    { key: "all", label: "All" },
    { key: "open", label: "Open" },
    { key: "under_review", label: "Under Review" },
    { key: "resolved", label: "Resolved" },
    { key: "deferred", label: "Deferred" },
];

export default function Conflicts() {
    const navigate = useNavigate();
    const [status, setStatus] = useState("all");
    const [cursor, setCursor] = useState(null);
    const [conflicts, setConflicts] = useState([]);
    const [nextCursor, setNextCursor] = useState(null);
    const selectedStatus = status === "all" ? null : status;
    const resource = useApiResource(
        async () => ({
            ...(await api.listConflicts(undefined, {
                status: selectedStatus,
                ...(cursor ? { cursor } : {}),
            })),
            requestedStatus: status,
            requestedCursor: cursor,
        }),
        { deps: [status, cursor], resetKey: `${status}:${cursor || "first"}` }
    );

    useEffect(() => {
        if (!resource.data) return;
        if (resource.data.requestedStatus !== status || resource.data.requestedCursor !== cursor) return;

        const page = Array.isArray(resource.data.conflicts) ? resource.data.conflicts : [];
        setConflicts((current) => {
            if (!cursor) return page;
            const seen = new Set(current.map((conflict) => conflict.id));
            return [...current, ...page.filter((conflict) => !seen.has(conflict.id))];
        });
        setNextCursor(resource.data.next_cursor || null);
    }, [cursor, resource.data, status]);

    useEffect(() => {
        if (!["auth", "forbidden", "missing"].includes(resource.error?.kind)) return;
        setConflicts([]);
        setNextCursor(null);
    }, [resource.error]);

    useEffect(() => onIdentityReset(() => {
        setCursor(null);
        setConflicts([]);
        setNextCursor(null);
    }), []);

    const selectStatus = (nextStatus) => {
        if (nextStatus === status) return;
        setStatus(nextStatus);
        setCursor(null);
        setConflicts([]);
        setNextCursor(null);
    };

    return (
        <>
            <TopBar
                title="Conflicts"
                subtitle={`${conflicts.length} ${status !== "all" ? `${status.replace("_", " ")} ` : ""}conflict${conflicts.length === 1 ? "" : "s"}`}
            />
            <div className="flex-1 px-8 py-6 space-y-5">
                <div className="flex items-center gap-1 bg-sm-surface border border-sm-border rounded-lg p-1 w-fit" data-testid="conflicts-tabs" role="group" aria-label="Filter by status">
                    {STATUS_TABS.map((tab) => (
                        <button
                            key={tab.key}
                            data-testid={`conflict-tab-${tab.key}`}
                            onClick={() => selectStatus(tab.key)}
                            aria-pressed={status === tab.key}
                            className={`h-8 px-3.5 rounded-md text-[12px] font-medium transition-colors ${
                                status === tab.key
                                    ? "bg-sm-blue/15 text-sm-blue"
                                    : "text-sm-text-secondary hover:text-sm-text"
                            }`}
                        >
                            {tab.label}
                        </button>
                    ))}
                </div>

                {resource.error && conflicts.length === 0 ? (
                    <PageLoadError error={resource.error} onRetry={resource.retry} testId="conflicts-error" />
                ) : resource.loading && conflicts.length === 0 ? (
                    <div className="space-y-3" role="status" aria-label="Loading conflicts">
                        {Array.from({ length: 3 }).map((_, index) => <div key={index} className="sm-card h-[180px] shimmer" />)}
                    </div>
                ) : conflicts.length === 0 ? (
                    <div className="sm-card py-20 text-center">
                        <AlertTriangle className="w-10 h-10 text-sm-text-muted mx-auto mb-3" strokeWidth={1.5} />
                        <p className="text-sm-text">No conflicts in this state.</p>
                        <p className="text-[12px] text-sm-text-secondary mt-1">Nothing has been flagged for this filter.</p>
                    </div>
                ) : (
                    <div className="space-y-3">
                        {conflicts.map((conflict) => (
                            <ConflictRow
                                key={conflict.id}
                                conflict={conflict}
                                onOpen={() => navigate(`/conflicts/${conflict.id}`)}
                            />
                        ))}
                        {resource.error && (
                            <PageLoadError error={resource.error} onRetry={resource.retry} testId="conflicts-page-error" compact />
                        )}
                        {nextCursor && !resource.error && (
                            <button
                                type="button"
                                data-testid="conflicts-load-more"
                                disabled={resource.loading}
                                onClick={() => setCursor(nextCursor)}
                                className="w-full h-10 rounded-md border border-sm-border bg-sm-surface text-[12px] font-medium text-sm-text-secondary hover:text-sm-text hover:border-sm-border-hover disabled:opacity-50"
                            >
                                {resource.loading ? "Loading more conflicts…" : "Load more conflicts"}
                            </button>
                        )}
                    </div>
                )}
            </div>
        </>
    );
}

function ConflictRow({ conflict, onOpen }) {
    const severity = conflict.severity || null;
    const similarity = typeof conflict.similarity_score === "number"
        ? Math.round(conflict.similarity_score * 100)
        : null;

    return (
        <article
            data-testid={`conflict-card-${conflict.id}`}
            onClick={onOpen}
            onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onOpen();
                }
            }}
            role="link"
            tabIndex={0}
            className="sm-card p-5 cursor-pointer"
        >
            <div className="flex items-center justify-between gap-4 mb-3">
                <div className="flex items-center gap-2.5 min-w-0">
                    <StatusBadge status={conflict.status} />
                    <span className="font-mono text-[11px] text-sm-text-secondary truncate">{conflict.id}</span>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                    {severity && (
                        <span data-testid={`conflict-severity-${severity}`} className="font-mono text-[10.5px] uppercase tracking-wider" style={{ color: severityColor(severity) }}>
                            ● {severity} severity
                        </span>
                    )}
                    <span className="font-mono text-[11px] text-sm-text-secondary">
                        {conflict.created_at ? relativeTime(conflict.created_at) : "—"}
                    </span>
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_1fr] gap-3 items-start mb-4">
                <ConflictExcerpt text={conflict.memory_a_content} label="MEMORY A" color="#4F7EFF" />
                <div className="flex items-center justify-center">
                    <span className="font-mono text-[10.5px] text-sm-text-muted uppercase tracking-wider px-2 py-1 rounded bg-sm-border/30">vs</span>
                </div>
                <ConflictExcerpt text={conflict.memory_b_content} label="MEMORY B" color="#A78BFA" />
            </div>

            {conflict.explanation && <p className="text-[12.5px] text-sm-text-secondary leading-snug mb-4">{conflict.explanation}</p>}

            <div className="flex items-center justify-between pt-3 border-t border-sm-border">
                <div className="flex items-center gap-3">
                    {conflict.conflict_type && <span className="font-mono text-[11px] text-sm-text-secondary">{conflict.conflict_type}</span>}
                    {similarity !== null && <span className="font-mono text-[10.5px] text-sm-text-muted">Similarity {similarity}%</span>}
                </div>
                <button
                    data-testid={`start-review-${conflict.id}`}
                    className="h-8 px-3 rounded-md bg-sm-blue/15 border border-sm-blue/30 text-sm-blue text-[11.5px] font-medium hover:bg-sm-blue/25"
                    onClick={(event) => { event.stopPropagation(); onOpen(); }}
                >
                    Start Review →
                </button>
            </div>
        </article>
    );
}

function ConflictExcerpt({ text, label, color }) {
    return (
        <div className="rounded-lg border border-sm-border bg-sm-bg/40 p-3">
            <div className="font-mono text-[10px] uppercase tracking-wider mb-1.5" style={{ color }}>{label}</div>
            <p className="text-[12.5px] text-sm-text leading-snug line-clamp-3">{text || "—"}</p>
        </div>
    );
}
