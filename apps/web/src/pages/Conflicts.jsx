import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ShieldCheck } from "lucide-react";
import PageHeader from "../components/ui-kit/PageHeader";
import EmptyState from "../components/ui-kit/EmptyState";
import InlineBar from "../components/ui-kit/InlineBar";
import { Skeleton } from "../components/ui-kit/Skeleton";
import ErrorState from "../components/ui-kit/ErrorState";
import useApiResource from "../hooks/useApiResource";
import StatusBadge from "../components/widgets/StatusBadge";
import api from "../lib/api";
import { relativeTime, severityColor } from "../lib/format";

/**
 * Page 5 of the Supermemory-console redesign.
 *
 * The workspace currently holds zero conflicts, so nothing here throws today.
 * That is NOT the same as correct — WORKING_STANDARDS rule 7. Verified against
 * a real populated response (one conflict created in a disposable workspace)
 * and against the authoritative response model, ConflictSummary in
 * apps/api/sourcemind/schemas/conflict.py. Four field mismatches:
 *
 *   reads              actual                     effect once a conflict exists
 *   -----------------  -------------------------  ------------------------------
 *   c.contributors     (does not exist)           .map() throws — page crashes
 *   c.detected_at      created_at                 "Invalid Date"
 *   c.memory_a_excerpt memory_a_content           blank excerpt
 *   c.memory_b_excerpt memory_b_content           blank excerpt
 *
 * `contributors` is not a field the API can supply at all — ConflictSummary
 * exposes memory ids and contents, not authorship — so the ContributorStack is
 * removed rather than re-pointed. Deriving it would need a per-memory
 * attribution fetch, which is a data-layer change and out of scope here.
 *
 * The list response also carries `similarity_score` and `explanation`, both
 * previously unused; they are now shown, since they are what actually explains
 * why two memories were flagged.
 */

const STATUS_TABS = [
    { key: "all",          label: "All"          },
    { key: "open",         label: "Open"         },
    { key: "under_review", label: "Under review" },
    { key: "resolved",     label: "Resolved"     },
    { key: "deferred",     label: "Deferred"     },
];

export default function Conflicts() {
    const navigate = useNavigate();
    const [status, setStatus] = useState("all");
    // `.catch(() => setConflicts([]))` previously turned 401/403/404/429 and
    // an offline browser into "No conflicts yet" — indistinguishable from a
    // genuinely clean workspace.
    const { data, error, loading, retry } = useApiResource(
        // "all" is a UI-only sentinel, not a status the API knows. The route
        // does `if conflict_status:` and appends `mc.status = :status`, so the
        // truthy string "all" filters for a literal status of 'all' and
        // matches nothing. Passing null omits the parameter entirely, because
        // realApi's request() skips null params while a destructuring default
        // would substitute "all" back in for undefined.
        () => api.listConflicts(undefined, { status: status === "all" ? null : status }),
        { resetKey: status }
    );

    const conflicts = Array.isArray(data?.conflicts) ? data.conflicts : null;

    const rows = conflicts ?? [];
    const stateWord = status !== "all" ? `${status.replace("_", " ")} ` : "";

    return (
        <>
            <PageHeader
                title="Conflicts"
                subtitle="Mutually exclusive claims detected between memories, and where each one stands."
            />

            <div
                className="flex items-center gap-1 bg-surface border border-hairline rounded-md p-1 w-fit mb-5"
                data-testid="conflicts-tabs"
                role="group"
                aria-label="Filter by status"
            >
                {STATUS_TABS.map((t) => (
                    <button
                        key={t.key}
                        data-testid={`conflict-tab-${t.key}`}
                        onClick={() => setStatus(t.key)}
                        aria-pressed={status === t.key}
                        /* §2: an active tab pill is on the accent's permitted list. */
                        className={`h-8 px-3.5 rounded-md text-body font-medium transition-colors sm-focusable ${
                            status === t.key
                                ? "bg-brand/10 text-brand"
                                : "text-content-secondary hover:text-content hover:bg-white/[0.03]"
                        }`}
                    >
                        {t.label}
                    </button>
                ))}
            </div>

            {error ? (
                <div className="sm-card">
                    <ErrorState error={error} onRetry={retry} />
                </div>
            ) : loading ? (
                /* §6: skeletons, never spinners, shaped like the real card. */
                <div className="space-y-3" role="status" aria-busy="true" aria-label="Loading conflicts">
                    {Array.from({ length: 3 }).map((_, i) => (
                        <div key={i} className="sm-card p-5 space-y-4">
                            <div className="flex justify-between">
                                <Skeleton className="h-5 w-40 rounded-md" />
                                <Skeleton className="h-5 w-32 rounded-md" />
                            </div>
                            <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_1fr] gap-3">
                                <Skeleton className="h-[72px] rounded-md" />
                                <Skeleton className="h-[72px] w-10 rounded-md" />
                                <Skeleton className="h-[72px] rounded-md" />
                            </div>
                            <Skeleton className="h-4 w-48" />
                        </div>
                    ))}
                    <span className="sr-only">Loading conflicts…</span>
                </div>
            ) : rows.length === 0 ? (
                /* §5, exact template: centred outline icon -> bold "No ___ yet"
                   headline -> one grey explanatory sentence. No action button:
                   there is nothing to link to until a conflict is detected, and
                   the reference marks the button optional. */
                <div className="sm-card">
                    <EmptyState
                        testId="conflicts-empty"
                        icon={ShieldCheck}
                        headline={
                            status === "all"
                                ? "No conflicts yet"
                                : `No ${status.replace("_", " ")} conflicts yet`
                        }
                        description={
                            status === "all"
                                ? "Conflicts are flagged when two contributors make mutually exclusive claims about the same decision. Nothing has been flagged in this workspace."
                                : "Nothing sits in this state right now. Switch the filter to see conflicts at other stages."
                        }
                    />
                </div>
            ) : (
                <>
                    <div className="space-y-3">
                        {rows.map((c) => (
                            <ConflictRow key={c.id} c={c} onOpen={() => navigate(`/conflicts/${c.id}`)} />
                        ))}
                    </div>
                    <div className="flex items-center justify-between mt-5 pt-4 border-t border-hairline">
                        <span className="font-mono text-[11px] text-content-secondary">
                            {rows.length} {stateWord}conflict{rows.length === 1 ? "" : "s"}
                        </span>
                    </div>
                </>
            )}
        </>
    );
}

function ConflictRow({ c, onOpen }) {
    const severity = c.severity || null;
    // similarity_score is 0–1 on the wire.
    const sim = typeof c.similarity_score === "number"
        ? Math.round(c.similarity_score * 100)
        : null;

    return (
        <article
            data-testid={`conflict-card-${c.id}`}
            onClick={onOpen}
            onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(); }
            }}
            role="link"
            tabIndex={0}
            className="sm-card p-5 cursor-pointer hover:border-hairline-hover transition-colors sm-focusable"
        >
            <div className="flex items-center justify-between gap-4 mb-4">
                <div className="flex items-center gap-2.5 min-w-0">
                    <StatusBadge status={c.status} />
                    <span className="font-mono text-[11px] text-content-secondary truncate">
                        {c.id}
                    </span>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                    {severity && (
                        /* §2: severity is semantic, so it is one of the three
                           status tokens — low/medium/critical map to
                           success/warning/danger. Never decorative. */
                        <span
                            data-testid={`conflict-severity-${severity}`}
                            className="font-mono text-[10.5px] uppercase tracking-wider"
                            style={{ color: severityColor(severity) }}
                        >
                            ● {severity}
                        </span>
                    )}
                    <span className="font-mono text-[11px] text-content-secondary">
                        {c.created_at ? relativeTime(c.created_at) : "—"}
                    </span>
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_1fr] gap-3 items-stretch mb-4">
                <Excerpt label="Memory A" text={c.memory_a_content} />
                <div className="flex items-center justify-center">
                    <span className="sm-micro-label px-2 py-1 rounded bg-surface-page border border-hairline">
                        vs
                    </span>
                </div>
                <Excerpt label="Memory B" text={c.memory_b_content} />
            </div>

            {c.explanation && (
                <p className="text-body text-content-secondary leading-snug mb-4">
                    {c.explanation}
                </p>
            )}

            <div className="flex items-center justify-between gap-4 pt-3 border-t border-hairline">
                <div className="flex items-center gap-4 min-w-0">
                    {c.conflict_type && (
                        <span className="sm-micro-label">{c.conflict_type}</span>
                    )}
                    {sim !== null && (
                        <div className="flex items-center gap-2">
                            <span className="sm-micro-label">Similarity</span>
                            {/* §4.5 — number and bar in one cell. */}
                            <InlineBar value={sim} width="56px" />
                        </div>
                    )}
                </div>
                <button
                    data-testid={`start-review-${c.id}`}
                    onClick={(e) => { e.stopPropagation(); onOpen(); }}
                    className="h-8 px-3 rounded-md border border-hairline bg-surface
                               hover:border-hairline-hover text-body font-medium text-content
                               transition-colors shrink-0 sm-focusable"
                >
                    Review →
                </button>
            </div>
        </article>
    );
}

function Excerpt({ label, text }) {
    return (
        <div className="rounded-md border border-hairline bg-surface-page p-3">
            <div className="sm-micro-label mb-1.5">{label}</div>
            {/* §3: memory content is data → mono. */}
            <p className="font-mono text-[12px] text-content leading-snug line-clamp-3">
                {text || "—"}
            </p>
        </div>
    );
}
