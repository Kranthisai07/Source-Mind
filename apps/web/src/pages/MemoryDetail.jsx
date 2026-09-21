import React from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
    ArrowLeft, Edit3, Trash2, AlertTriangle, Share2, History, Users,
} from "lucide-react";
import PageHeader from "../components/ui-kit/PageHeader";
import EmptyState from "../components/ui-kit/EmptyState";
import InlineBar from "../components/ui-kit/InlineBar";
import { Skeleton } from "../components/ui-kit/Skeleton";
import ErrorState from "../components/ui-kit/ErrorState";
import useApiResource from "../hooks/useApiResource";
import Markdown from "../components/widgets/Markdown";
import { Button } from "../components/ui/button";
import api from "../lib/api";
import { relativeTime, formatDate, initials } from "../lib/format";

/**
 * Page 2 of the Supermemory-console redesign.
 *
 * Same single data call, same response shape. What changed:
 *
 * - §4.5 replaces the 180px Recharts donut. The brief is explicit that
 *   attribution percentages get "the number AND a compact horizontal bar in
 *   the same cell, not a separate large gauge", so the PieChart/Pie/Cell import
 *   is gone entirely and each contributor row carries its own inline bar.
 * - §2 removes the per-contributor colour. adaptAttribution assigns each
 *   contributor one of eight hues from a decorative palette; the reference
 *   permits one accent and reserves green/red for meaning. Identity is carried
 *   by the name and initials instead.
 * - §3 puts the memory content, the id and the contributor handles in the mono
 *   face ("memory content excerpts, IDs ... a clear 'this is technical /
 *   copyable' signal"), and table column headers at 11px uppercase.
 * - §6 skeletons sized to the real layout, replacing the two `shimmer` blocks.
 * - §5 empty-state template for attribution and for the version timeline.
 *
 * Three real bugs the live payload exposed, all pre-dating this redesign:
 *
 *   1. `mem.tags.map(...)` was unguarded and `tags` is null on real memories,
 *      so this page threw "Cannot read properties of null" outright.
 *   2. The H1 rendered `mem.memory_id`, which does not exist — the field is
 *      `id`, so the title was blank.
 *   3. `mem.category` is null on real memories and was rendered twice
 *      unconditionally, producing an empty badge and a stray "·" in the
 *      subtitle.
 *
 * CONTRIBUTORS from mockData is no longer imported. It was used to look up
 * version editors, i.e. fabricated people rendered beside real data — the same
 * mock-era-literal-at-a-call-site class found earlier this session.
 */
export default function MemoryDetail() {
    const { id } = useParams();
    const navigate = useNavigate();
    // resetKey = id: navigating to another memory clears the previous one
    // before the new request lands, so a slow response for the memory you just
    // left cannot repopulate the page you are now on.
    const { data: mem, error, loading, retry } = useApiResource(
        () => api.getMemory(id),
        { resetKey: id }
    );

    // Version history is a separate endpoint, so it gets its own lifecycle: a
    // failure here must not blank the memory itself, and an empty chain must
    // not look like a failed one. resetKey = id so switching memories clears
    // the previous chain before the new request lands.
    const versionsRes = useApiResource(
        () => api.getMemoryVersions(id),
        { resetKey: id }
    );

    if (error) {
        return (
            <>
                <PageHeader title="Memory" subtitle="This memory could not be loaded." />
                <div className="sm-card">
                    <ErrorState error={error} onRetry={retry} />
                </div>
            </>
        );
    }

    if (loading || !mem) {
        return (
            <>
                <PageHeader title="Memory" subtitle="Loading…" />
                <div
                    className="grid grid-cols-1 lg:grid-cols-3 gap-4"
                    role="status"
                    aria-busy="true"
                    aria-label="Loading memory"
                >
                    <div className="sm-card p-8 lg:col-span-2 space-y-4">
                        <div className="flex gap-1.5">
                            <Skeleton className="h-5 w-16 rounded-md" />
                            <Skeleton className="h-5 w-20 rounded-md" />
                        </div>
                        <Skeleton className="h-[1em] w-full" />
                        <Skeleton className="h-[1em] w-full" />
                        <Skeleton className="h-[1em] w-3/5" />
                        <div className="pt-8 space-y-3">
                            <Skeleton className="h-4 w-40" />
                            <Skeleton className="h-tablerow w-full rounded-md" />
                            <Skeleton className="h-tablerow w-full rounded-md" />
                        </div>
                    </div>
                    <div className="sm-card p-6 space-y-4">
                        <Skeleton className="h-4 w-32" />
                        <Skeleton className="h-16 w-full rounded-md" />
                        <Skeleton className="h-16 w-full rounded-md" />
                    </div>
                    <span className="sr-only">Loading memory…</span>
                </div>
            </>
        );
    }

    // realApi normalises ContributionBreakdown[] into this shape and returns
    // [] when the backend reports none; mockApi already produces it. Both can
    // still be empty, so nothing below may index without checking.
    const attribution = Array.isArray(mem.attribution) ? mem.attribution : [];
    const primary = attribution.find(a => a.is_primary) || attribution[0] || null;
    const primaryPct = primary
        ? Math.round(primary.percentage ?? (primary.score ?? 0) * 100)
        : null;

    // The chain comes from /v1/memories/{id}/versions, not from the memory
    // object — `mem.versions` does not exist on that response. Three distinct
    // states below: loading, failed, and genuinely empty.
    const versions = Array.isArray(versionsRes.data?.versions)
        ? versionsRes.data.versions
        : [];

    const tags = Array.isArray(mem.tags) ? mem.tags : [];
    const memoryId = mem.id || mem.memory_id || id;

    const subtitleParts = [
        mem.category,
        mem.version != null ? `v${mem.version}` : null,
        mem.created_at ? `created ${relativeTime(mem.created_at)}` : null,
    ].filter(Boolean);

    return (
        <>
            <PageHeader
                title="Memory"
                subtitle={subtitleParts.join(" · ") || "No metadata recorded"}
                action={
                    <>
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => navigate(-1)}
                            className="text-content-secondary"
                            data-testid="mem-back"
                        >
                            <ArrowLeft className="w-4 h-4" /> Back
                        </Button>
                        {/* Disabled, not removed — the treatment Handoff's
                            Assign/Complete buttons already use. These three had
                            no onClick at all: they took hover styling, took the
                            click, and did nothing. Delete was the worst of the
                            three, because `text-danger` advertises a
                            destructive action, so a user who believed it had
                            worked would believe the memory was gone.

                            Wiring them is a data-layer change and stays out of
                            scope. What changes here is that the screen stops
                            claiming they exist. */}
                        <Button
                            variant="outline"
                            size="sm"
                            disabled
                            data-testid="mem-edit"
                            title="Editing a memory isn't available on this screen yet"
                            className="bg-white/[0.04] border-hairline text-content disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                            <Edit3 className="w-3.5 h-3.5" /> Edit
                        </Button>
                        <Button
                            variant="outline"
                            size="sm"
                            disabled
                            data-testid="mem-share"
                            title="Sharing a memory isn't available on this screen yet"
                            className="bg-white/[0.04] border-hairline text-content disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                            <Share2 className="w-3.5 h-3.5" /> Share
                        </Button>
                        {/* §2: red is reserved for destructive. */}
                        <Button
                            variant="outline"
                            size="sm"
                            disabled
                            data-testid="mem-delete"
                            title="Deleting a memory isn't available on this screen yet"
                            className="bg-white/[0.04] border-hairline text-danger hover:text-danger disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                            <Trash2 className="w-3.5 h-3.5" /> Delete
                        </Button>
                    </>
                }
            />

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                <section className="sm-card p-8 lg:col-span-2">
                    {/* §3: the id is technical and copyable → mono. */}
                    <div className="flex items-center gap-2 flex-wrap mb-5">
                        <span className="sm-micro-label">ID</span>
                        <code className="font-mono text-[11px] text-content-secondary select-all">
                            {memoryId}
                        </code>
                        {mem.category && (
                            <span className="font-mono text-[10.5px] px-2 py-0.5 rounded-md border border-brand/30 bg-brand/10 text-brand ml-auto">
                                {mem.category}
                            </span>
                        )}
                    </div>

                    {tags.length > 0 && (
                        <div className="flex items-center gap-1.5 flex-wrap mb-5">
                            {tags.map(t => (
                                <span
                                    key={t}
                                    className="font-mono text-[10.5px] px-2 py-0.5 rounded-md border border-hairline text-content-secondary"
                                >
                                    {t}
                                </span>
                            ))}
                        </div>
                    )}

                    {/* §3: "Monospace specifically for: memory content excerpts
                        ... a clear 'this is technical/copyable' signal distinct
                        from the sans-serif UI text." */}
                    <article className="prose prose-invert max-w-none font-mono text-[13px] leading-relaxed">
                        <Markdown>{mem.content}</Markdown>
                    </article>

                    <div className="mt-8 pt-6 border-t border-hairline flex items-center justify-between gap-4">
                        <div className="flex items-center gap-3 min-w-0">
                            {/* §2: neutral initials, not a per-person hue. */}
                            <div className="w-8 h-8 rounded-full shrink-0 flex items-center justify-center
                                            bg-surface-hover border border-hairline
                                            text-[11px] font-semibold text-content-secondary">
                                {primary ? initials(primary.name || primary.author) : "—"}
                            </div>
                            <div className="min-w-0">
                                <div className="text-body font-medium text-content truncate">
                                    {primary?.name || primary?.author || "Unattributed"}
                                </div>
                                <div className="sm-micro-label">
                                    {primary ? "Primary author" : "No attribution recorded"}
                                </div>
                            </div>
                        </div>
                        <div className="text-right shrink-0">
                            <div className="sm-micro-label">Created</div>
                            <div className="font-mono text-body text-content mt-0.5">
                                {mem.created_at ? formatDate(mem.created_at) : "—"}
                            </div>
                        </div>
                    </div>

                    <div className="mt-8">
                        <div className="flex items-baseline justify-between gap-4 mb-4">
                            <h2 className="text-title text-content">Attribution</h2>
                            {/* §3 hero metric: the primary contributor's share
                                is the number this section exists to convey. */}
                            {primaryPct !== null && (
                                <div className="flex items-baseline gap-1.5">
                                    <span
                                        data-testid="primary-pct"
                                        className="font-mono text-hero tabular-nums text-content"
                                    >
                                        {primaryPct}
                                    </span>
                                    <span className="text-body text-content-secondary">% primary</span>
                                </div>
                            )}
                        </div>

                        {attribution.length === 0 ? (
                            <EmptyState
                                testId="attribution-empty"
                                icon={Users}
                                noun="attribution"
                                description="Once the five-signal engine scores this memory, each contributor's share appears here."
                            />
                        ) : (
                            <table className="w-full">
                                <thead>
                                    {/* §3: "micro-labels (~11px, uppercase,
                                        letter-spaced — used for table column
                                        headers)". */}
                                    <tr className="text-left border-b border-hairline">
                                        <th className="sm-micro-label font-semibold pb-2">Contributor</th>
                                        <th className="sm-micro-label font-semibold pb-2 w-[180px]">Share</th>
                                        <th className="sm-micro-label font-semibold pb-2 text-right">Signals</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {attribution.map((a, i) => (
                                        <tr
                                            key={i}
                                            data-testid={`attribution-row-${i}`}
                                            className="border-b border-hairline last:border-0"
                                        >
                                            <td className="py-3">
                                                <div className="flex items-center gap-2.5 min-w-0">
                                                    <div className="w-6 h-6 rounded-full shrink-0 flex items-center justify-center
                                                                    bg-surface-hover border border-hairline
                                                                    text-[9px] font-semibold text-content-secondary">
                                                        {initials(a.name || a.author)}
                                                    </div>
                                                    <span className="font-mono text-body text-content truncate">
                                                        {a.author}
                                                    </span>
                                                    {a.is_primary && (
                                                        <span className="sm-micro-label shrink-0">Primary</span>
                                                    )}
                                                </div>
                                            </td>
                                            {/* §4.5 — number and bar, one cell. */}
                                            <td className="py-3">
                                                <InlineBar
                                                    value={Math.round(a.percentage ?? (a.score ?? 0) * 100)}
                                                    width="96px"
                                                />
                                            </td>
                                            <td className="py-3 text-right font-mono text-[10.5px] text-content-secondary">
                                                {a.signals
                                                    ? Object.values(a.signals)
                                                          .filter(v => typeof v === "number")
                                                          .map(v => v.toFixed(2))
                                                          .join(" · ")
                                                    : "—"}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                    </div>
                </section>

                <aside className="sm-card p-6 flex flex-col">
                    <h2 className="text-title text-content mb-5">Version history</h2>

                    {versionsRes.loading ? (
                        <div className="space-y-3" role="status" aria-busy="true" aria-label="Loading version history">
                            {Array.from({ length: 2 }).map((_, i) => (
                                <Skeleton key={i} className="h-12 w-full rounded-md" />
                            ))}
                            <span className="sr-only">Loading version history…</span>
                        </div>
                    ) : versionsRes.error ? (
                        /* A failed history is NOT an empty one. Without this the
                           404 a non-member receives would read as "this memory
                           has never been edited". */
                        <ErrorState
                            error={versionsRes.error}
                            onRetry={versionsRes.retry}
                            testId="versions-error"
                        />
                    ) : versions.length === 0 ? (
                        <EmptyState
                            testId="versions-empty"
                            icon={History}
                            noun="earlier versions"
                            description="Editing this memory creates a new version; the chain of edits and who made them will appear here."
                        />
                    ) : (
                        <ol className="relative border-l border-hairline ml-2 space-y-5">
                            {/* MemoryVersionEntry: {id, version, is_current,
                                content, created_at}. There is no editor field
                                on this response, so none is invented. */}
                            {versions.map((v) => (
                                <li key={v.id ?? v.version} className="pl-5 relative">
                                    <span className={`absolute -left-[4.5px] top-1.5 w-2 h-2 rounded-full ${
                                        v.is_current ? "bg-brand" : "bg-content-muted"
                                    }`} />
                                    <div className="flex items-center gap-2 mb-1">
                                        <span className="font-mono text-[11px] text-brand">v{v.version}</span>
                                        {v.is_current && <span className="sm-micro-label">Current</span>}
                                        {v.created_at && (
                                            <span className="font-mono text-[10.5px] text-content-secondary">
                                                {relativeTime(v.created_at)}
                                            </span>
                                        )}
                                    </div>
                                    {v.content && (
                                        <p className="font-mono text-[11.5px] text-content-secondary mt-0.5 line-clamp-2">
                                            {v.content}
                                        </p>
                                    )}
                                </li>
                            ))}
                        </ol>
                    )}

                    <Button
                        variant="outline"
                        size="sm"
                        className="w-full mt-6 bg-white/[0.03] border-hairline text-content-secondary hover:text-content"
                        onClick={() => navigate("/conflicts")}
                    >
                        <AlertTriangle className="w-3.5 h-3.5" /> View conflicts
                    </Button>
                </aside>
            </div>
        </>
    );
}
