import React from "react";
import { Brain, Users, TrendingUp, AlertCircle, ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";
import PageHeader from "../components/ui-kit/PageHeader";
import StatCard from "../components/ui-kit/StatCard";
import InlineBar from "../components/ui-kit/InlineBar";
import EmptyState from "../components/ui-kit/EmptyState";
import { Skeleton } from "../components/ui-kit/Skeleton";
import ErrorState from "../components/ui-kit/ErrorState";
import useApiResource from "../hooks/useApiResource";
import RiskBadge from "../components/widgets/RiskBadge";
import WhoWouldKnow from "../components/widgets/WhoWouldKnow";
import api from "../lib/api";
import { relativeTime } from "../lib/format";

/**
 * Page 1 of the Supermemory-console redesign.
 *
 * Data layer is untouched: the same two calls, the same response shapes, the
 * same fields. Everything below the fetch is visual.
 *
 * Design decisions traceable to the reference:
 *
 * - §3 hero metric on total_memories: the single most important number on the
 *   view, at 34px semibold mono, versus its ~11px uppercase label.
 * - §2 one accent hue. The four stat cards previously carried four different
 *   accent colours (blue/purple/green/amber) and the health breakdown was four
 *   more. Both are now the blue ramp stepped by opacity, with colour used only
 *   where it is semantic (open conflicts > 0 is a warning).
 * - §4.5 "duration as mini-bar": every health-breakdown row and contributor
 *   row shows the number AND a compact bar in the same cell, replacing the
 *   180px HealthGauge dial. That gauge is why HealthGauge is no longer
 *   imported here.
 * - §6 skeletons, never spinners, sized to the content they replace.
 * - §5 empty-state template for the two lists that can legitimately be empty.
 *
 * §4.1 note: Supermemory's own Overview has no metrics widget at all — metrics
 * live on Requests/Billing. That is deliberately NOT followed here, because
 * the brief asks for the hero-metric treatment on this page's total_memories,
 * and SourceMind has no separate usage screen to move them to.
 */

const HEALTH_WEIGHTS = [
    { key: "coverage",             label: "Coverage",        weight: "30%", tone: "data-1" },
    { key: "freshness",            label: "Freshness",       weight: "30%", tone: "data-2" },
    { key: "conflict_ratio",       label: "Conflict ratio",  weight: "25%", tone: "data-3" },
    { key: "attribution_coverage", label: "Attribution",     weight: "15%", tone: "data-4" },
];

export default function Dashboard() {
    // Previously `.then(setData)` with no `.catch`. A rejected request left
    // `data` null forever, so a revoked member saw the loading skeleton
    // permanently rather than being told what happened.
    const overview = useApiResource(() => api.getAnalyticsOverview());
    const gapsRes = useApiResource(() => api.getKnowledgeGaps());

    const data = overview.data;
    const gaps = gapsRes.data?.gaps ?? null;
    const loading = overview.loading;
    const error = overview.error;

    return (
        <>
            <PageHeader
                title="Dashboard"
                subtitle="Workspace knowledge at a glance — coverage, contributors, and what needs attention."
                action={
                    <Link to="/memories">
                        <button
                            data-testid="dashboard-browse-btn"
                            className="h-9 px-3.5 rounded-md border border-hairline bg-surface
                                       hover:border-hairline-hover text-body font-medium text-content
                                       transition-colors flex items-center gap-1.5 sm-focusable"
                        >
                            Browse memories
                            <ArrowRight className="w-3.5 h-3.5" aria-hidden="true" />
                        </button>
                    </Link>
                }
            />

            {error ? (
                <div className="sm-card">
                    <ErrorState error={error} onRetry={overview.retry} />
                </div>
            ) : (
            <div className="space-y-6">
                {/* Row 1 — stat cards. §3: hero metric first. */}
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                    <StatCard
                        testId="metric-total-memories"
                        label="Total memories"
                        icon={Brain}
                        loading={loading}
                        value={data?.total_memories?.toLocaleString() ?? "—"}
                        caption={`Across ${data?.total_contributors ?? "—"} contributors`}
                    />
                    <StatCard
                        testId="metric-contributors"
                        label="Contributors"
                        icon={Users}
                        loading={loading}
                        value={data?.total_contributors ?? "—"}
                        caption="Attributed in this workspace"
                    />
                    <StatCard
                        testId="metric-new-this-month"
                        label="New in 30 days"
                        icon={TrendingUp}
                        loading={loading}
                        value={data?.memories_created_last_30_days ?? "—"}
                        caption="Memories created"
                    />
                    <StatCard
                        testId="metric-open-conflicts"
                        label="Open conflicts"
                        icon={AlertCircle}
                        loading={loading}
                        /* §2: colour only where semantic. */
                        tone={data?.open_conflicts > 0 ? "warning" : "success"}
                        value={data?.open_conflicts ?? "—"}
                        caption={
                            data?.open_conflicts > 0 ? (
                                <Link to="/conflicts" className="text-brand hover:underline">
                                    Review now →
                                </Link>
                            ) : (
                                "Nothing to review"
                            )
                        }
                    />
                </div>

                {/* Row 2 — health breakdown + activity */}
                <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
                    <section className="sm-card p-6 lg:col-span-3">
                        <div className="flex items-start justify-between gap-4 mb-5">
                            <div>
                                <h2 className="text-title text-content">Knowledge health</h2>
                                <p className="text-body text-content-secondary mt-0.5">
                                    Weighted composite of four signals
                                </p>
                            </div>
                            <Link to="/analytics" className="text-body text-brand hover:underline shrink-0">
                                View details →
                            </Link>
                        </div>

                        {/* §3 hero metric — the composite score is the point of
                            this card, so it gets the outsized numeral rather
                            than a decorative dial. */}
                        <div className="flex items-baseline gap-2 mb-6">
                            {loading ? (
                                <Skeleton className="h-[34px] w-28" />
                            ) : (
                                <>
                                    <span
                                        data-testid="health-score"
                                        className="font-mono text-hero tabular-nums text-content"
                                    >
                                        {data?.knowledge_health_score ?? "—"}
                                    </span>
                                    <span className="text-body-lg text-content-secondary">/ 100</span>
                                </>
                            )}
                        </div>

                        <div className="space-y-4">
                            {HEALTH_WEIGHTS.map(({ key, label, weight, tone }) => (
                                <div key={key} className="flex items-center gap-4">
                                    <div className="w-40 shrink-0 flex items-baseline gap-1.5">
                                        <span className="text-body text-content">{label}</span>
                                        <span className="font-mono text-[10px] text-content-muted">
                                            {weight}
                                        </span>
                                    </div>
                                    {loading ? (
                                        <Skeleton className="h-[1em] flex-1" />
                                    ) : (
                                        /* §4.5 — number and bar in one cell. */
                                        <InlineBar
                                            testId={`health-${key}`}
                                            value={data?.health_breakdown?.[key] ?? 0}
                                            width="100%"
                                            className="flex-1"
                                            barColor={`var(--c-${tone})`}
                                        />
                                    )}
                                </div>
                            ))}
                        </div>
                    </section>

                    <section className="sm-card p-6 lg:col-span-2 flex flex-col">
                        <div className="flex items-center justify-between mb-5">
                            <h2 className="text-title text-content">Recent activity</h2>
                            <span className="sm-micro-label">Live</span>
                        </div>

                        {loading ? (
                            <div className="space-y-4" role="status" aria-busy="true" aria-label="Loading activity">
                                {Array.from({ length: 5 }).map((_, i) => (
                                    <div key={i} className="flex gap-3">
                                        <Skeleton className="w-1.5 h-1.5 rounded-full mt-1.5 shrink-0" />
                                        <div className="flex-1 space-y-1.5">
                                            <Skeleton className="h-[1em] w-full" />
                                            <Skeleton className="h-[0.85em] w-16" />
                                        </div>
                                    </div>
                                ))}
                                <span className="sr-only">Loading activity…</span>
                            </div>
                        ) : (data?.recent_activity ?? []).length === 0 ? (
                            <EmptyState
                                testId="activity-empty"
                                icon={TrendingUp}
                                noun="activity"
                                description="Ingesting memories or resolving conflicts will show up here as it happens."
                            />
                        ) : (
                            <div className="space-y-3.5 flex-1 overflow-y-auto max-h-[340px] pr-1">
                                {(data?.recent_activity ?? []).map((a, i) => (
                                    <div key={i} data-testid={`activity-${i}`} className="flex items-start gap-3">
                                        {/* §2: the activity dot was one of eight
                                            hues from a decorative palette. It is
                                            now a neutral marker — the label
                                            already says what happened. */}
                                        <span
                                            aria-hidden="true"
                                            className="w-1.5 h-1.5 rounded-full mt-[7px] shrink-0 bg-content-muted"
                                        />
                                        <div className="flex-1 min-w-0">
                                            <p className="text-body text-content leading-snug">
                                                {a.description}
                                            </p>
                                            <p className="font-mono text-[10.5px] text-content-secondary mt-1">
                                                {relativeTime(a.timestamp)}
                                            </p>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </section>
                </div>

                {/* Row 3 — expertise lookup (unchanged component, own data call) */}
                <WhoWouldKnow />

                {/* Row 4 — contributors + gaps */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    <section className="sm-card p-6">
                        <div className="flex items-center justify-between mb-5">
                            <h2 className="text-title text-content">Top contributors</h2>
                            <Link to="/analytics" className="text-body text-brand hover:underline">
                                Contribution map →
                            </Link>
                        </div>

                        {loading ? (
                            <div className="space-y-3" role="status" aria-busy="true" aria-label="Loading contributors">
                                {Array.from({ length: 5 }).map((_, i) => (
                                    <Skeleton key={i} className="h-6 w-full" />
                                ))}
                                <span className="sr-only">Loading contributors…</span>
                            </div>
                        ) : (data?.top_contributors ?? []).length === 0 ? (
                            <EmptyState
                                testId="contributors-empty"
                                icon={Users}
                                noun="contributors"
                                description="Once memories carry attribution, the people behind them appear here."
                            />
                        ) : (
                            <div className="space-y-3">
                                {(data?.top_contributors ?? []).map((c, i) => {
                                    const max = data.top_contributors[0].count || 1;
                                    return (
                                        <div
                                            key={c.name}
                                            data-testid={`top-contributor-${i}`}
                                            className="flex items-center gap-3"
                                        >
                                            <span className="font-mono text-[11px] text-content-muted w-4 text-right shrink-0">
                                                {i + 1}
                                            </span>
                                            {/* §3: names/handles are technical
                                                identifiers → monospace. */}
                                            <span className="font-mono text-body text-content flex-1 truncate">
                                                @{c.name}
                                            </span>
                                            {/* §4.5 pattern */}
                                            <InlineBar
                                                value={c.count}
                                                max={max}
                                                display={c.count}
                                                suffix=""
                                                width="72px"
                                            />
                                        </div>
                                    );
                                })}
                            </div>
                        )}
                    </section>

                    <section className="sm-card p-6">
                        <div className="flex items-center justify-between mb-5">
                            <h2 className="text-title text-content">Knowledge gaps</h2>
                            <Link
                                to="/analytics"
                                data-testid="dashboard-view-all-gaps"
                                className="text-body text-brand hover:underline"
                            >
                                View all →
                            </Link>
                        </div>

                        {gapsRes.error ? (
                            <ErrorState error={gapsRes.error} onRetry={gapsRes.retry} testId="gaps-error" />
                        ) : gaps === null ? (
                            <div className="space-y-3" role="status" aria-busy="true" aria-label="Loading knowledge gaps">
                                {Array.from({ length: 3 }).map((_, i) => (
                                    <Skeleton key={i} className="h-[92px] w-full rounded-md" />
                                ))}
                                <span className="sr-only">Loading knowledge gaps…</span>
                            </div>
                        ) : gaps.length === 0 ? (
                            <EmptyState
                                testId="gaps-empty"
                                icon={AlertCircle}
                                noun="knowledge gaps"
                                description="Single-owner topics, stale memories and high-conflict areas will be flagged here."
                            />
                        ) : (
                            <div className="space-y-3">
                                {gaps.slice(0, 3).map((g, i) => (
                                    <div
                                        key={i}
                                        data-testid={`gap-card-${i}`}
                                        className="rounded-md border border-hairline bg-surface-page p-4
                                                   hover:border-hairline-hover transition-colors"
                                    >
                                        <div className="flex items-center justify-between mb-2">
                                            <RiskBadge level={g.risk_level} />
                                            <span className="sm-micro-label">{g.gap_type}</span>
                                        </div>
                                        <p className="text-body text-content leading-snug">{g.description}</p>
                                        {/* The API field is `affected_memories`.
                                            This read `g.affected_count`, which
                                            does not exist on the response, so
                                            the line rendered as " affected
                                            memories" with no number. Pre-dates
                                            the redesign; caught by asserting
                                            the field contract against the live
                                            endpoint rather than assuming it. */}
                                        <p className="font-mono text-[10.5px] text-content-secondary mt-2">
                                            {(g.affected_memories ?? 0).toLocaleString()} affected memories
                                        </p>
                                    </div>
                                ))}
                            </div>
                        )}
                    </section>
                </div>
            </div>
            )}
        </>
    );
}
