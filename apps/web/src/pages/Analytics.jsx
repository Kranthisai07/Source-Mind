import React, { useState } from "react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis, LineChart, Line } from "recharts";
import { Filter, LineChart as LineChartIcon, Users, AlertCircle } from "lucide-react";
import PageHeader from "../components/ui-kit/PageHeader";
import EmptyState from "../components/ui-kit/EmptyState";
import InlineBar from "../components/ui-kit/InlineBar";
import { Skeleton } from "../components/ui-kit/Skeleton";
import ErrorState from "../components/ui-kit/ErrorState";
import useApiResource from "../hooks/useApiResource";
import RiskBadge from "../components/widgets/RiskBadge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../components/ui/tabs";
import api, { useMocks } from "../lib/api";
import { relativeTime } from "../lib/format";

/**
 * Page 4 of the Supermemory-console redesign.
 *
 * No data call changed. Four real bugs the live payloads exposed:
 *
 *   1. `g.affected_count` — the field is `affected_memories`. Rendered as a
 *      blank number. (Same bug reported at checkpoint 1 on Dashboard.)
 *   2. `g.recommended_action` — the field is `recommendation`. The whole
 *      recommendation line was empty.
 *   3. The risk filter compared `g.risk_level === "HIGH"` against an API that
 *      returns lowercase `"high"`, so every filter except ALL showed nothing.
 *   4. `top_category` is hardcoded to "" by adaptContributor, so that table
 *      column was permanently blank. Replaced with the contributor's share,
 *      which the payload actually carries.
 *
 * §4.5 replaces both large gauges: the 220px HealthGauge dial and the Treemap.
 * The reference puts a hero numeral where the number is the point and a
 * compact bar inline with it, and §2 forbids the Treemap's per-contributor
 * hues outright.
 *
 * Memories-over-time and Search-activity are rendered ONLY in mock mode. In
 * real mode realApi routes both to mockApi (the backend has no
 * /analytics/memories-over-time or /analytics/search-activity), so these
 * charts were drawing fabricated series beside real numbers. Rather than
 * change the data layer, the page asks `useMocks` and shows the §5 empty-state
 * template instead — the same "this is not available" honesty the rest of the
 * product needs.
 */
export default function Analytics() {
    const [riskFilter, setRiskFilter] = useState("ALL");

    // Each panel owns its own lifecycle: one failing call must not blank the
    // whole page, and none of them may leave a permanent skeleton.
    const overviewRes = useApiResource(() => api.getAnalyticsOverview());
    const gapsRes = useApiResource(() => api.getKnowledgeGaps());
    const contribRes = useApiResource(() => api.listContributors());
    const seriesRes = useApiResource(() => api.getMemoriesOverTime());
    const searchRes = useApiResource(() => api.getSearchActivity());

    const overview = overviewRes.data;
    const gaps = gapsRes.error ? [] : (gapsRes.data?.gaps ?? null);
    const contribs = contribRes.error ? [] : (contribRes.data?.contributors ?? null);
    const series = seriesRes.data?.series ?? [];
    const searchSeries = searchRes.data?.series ?? [];

    // The API returns lowercase risk levels; the filter labels are uppercase.
    const filteredGaps = (gaps ?? []).filter(
        g => riskFilter === "ALL"
            || String(g.risk_level || "").toUpperCase() === riskFilter
    );

    const hb = overview?.health_breakdown ?? {};

    return (
        <>
            <PageHeader
                title="Analytics"
                subtitle="Where knowledge lives, who holds it, and where the workspace is thin."
            />

            <Tabs defaultValue="overview" className="w-full">
                <TabsList
                    className="bg-surface border border-hairline p-1 h-10 mb-6"
                    data-testid="analytics-tabs"
                >
                    {[
                        ["overview", "Overview", "tab-overview"],
                        ["contribution", "Contribution map", "tab-contribution"],
                        ["gaps", "Knowledge gaps", "tab-gaps"],
                    ].map(([v, label, tid]) => (
                        <TabsTrigger
                            key={v}
                            value={v}
                            data-testid={tid}
                            /* §2: an active tab pill is on the accent's
                               permitted list. */
                            className="text-body text-content-secondary data-[state=active]:bg-brand/10 data-[state=active]:text-brand"
                        >
                            {label}
                        </TabsTrigger>
                    ))}
                </TabsList>

                {/* ─── OVERVIEW ─────────────────────────────────────────── */}
                <TabsContent value="overview" className="space-y-4 mt-0">
                    {overviewRes.error && (
                        <div className="sm-card">
                            <ErrorState error={overviewRes.error} onRetry={overviewRes.retry} testId="overview-error" />
                        </div>
                    )}
                    <section className="sm-card p-6">
                        <div className="flex items-start justify-between gap-6 mb-6">
                            <div>
                                <h2 className="text-title text-content">Knowledge health</h2>
                                <p className="text-body text-content-secondary mt-0.5">
                                    Weighted composite of four signals
                                </p>
                            </div>
                            {/* §3 hero metric, replacing the 220px dial. */}
                            <div className="flex items-baseline gap-1.5 shrink-0">
                                {overview === null ? (
                                    <Skeleton className="h-[34px] w-24" />
                                ) : (
                                    <>
                                        <span
                                            data-testid="analytics-health-score"
                                            className="font-mono text-hero tabular-nums text-content"
                                        >
                                            {overview.knowledge_health_score ?? "—"}
                                        </span>
                                        <span className="text-body-lg text-content-secondary">/ 100</span>
                                    </>
                                )}
                            </div>
                        </div>

                        <div className="space-y-5">
                            {[
                                ["coverage", "Coverage", "30%", "data-1",
                                 "How much of the team's activity is reflected in memories"],
                                ["freshness", "Freshness", "30%", "data-2",
                                 "How recently memories have been updated or reviewed"],
                                ["conflict_ratio", "Conflict ratio", "25%", "data-3",
                                 "Inverse of unresolved conflicts per active memory"],
                                ["attribution_coverage", "Attribution", "15%", "data-4",
                                 "Share of memories with high-confidence author attribution"],
                            ].map(([key, label, weight, tone, desc]) => (
                                <div key={key}>
                                    <div className="flex items-baseline justify-between gap-4 mb-1.5">
                                        <div className="min-w-0">
                                            <span className="text-body text-content font-medium">{label}</span>
                                            <span className="font-mono text-[10.5px] text-content-muted ml-2">
                                                weight {weight}
                                            </span>
                                        </div>
                                        {overview === null ? (
                                            <Skeleton className="h-[1em] w-24" />
                                        ) : (
                                            /* §4.5 — number and bar together. */
                                            <InlineBar
                                                testId={`analytics-health-${key}`}
                                                value={hb[key] ?? 0}
                                                width="140px"
                                                barColor={`var(--c-${tone})`}
                                            />
                                        )}
                                    </div>
                                    <p className="text-[11.5px] text-content-secondary">{desc}</p>
                                </div>
                            ))}
                        </div>
                    </section>

                    <div className="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-4">
                        <section className="sm-card p-6">
                            <div className="flex items-center justify-between mb-4">
                                <h2 className="text-title text-content">Memories over time</h2>
                                <span className="sm-micro-label">Last 30 days</span>
                            </div>
                            {!useMocks ? (
                                <EmptyState
                                    testId="series-unavailable"
                                    icon={LineChartIcon}
                                    headline="No time series yet"
                                    description="This chart needs a /analytics/memories-over-time endpoint, which the backend does not expose yet."
                                />
                            ) : (
                                <div className="h-[220px]">
                                    <ResponsiveContainer width="100%" height="100%">
                                        <AreaChart data={series} margin={{ top: 5, right: 10, bottom: 0, left: -20 }}>
                                            <defs>
                                                <linearGradient id="g1" x1="0" y1="0" x2="0" y2="1">
                                                    <stop offset="0%" stopColor="var(--c-accent)" stopOpacity={0.45} />
                                                    <stop offset="100%" stopColor="var(--c-accent)" stopOpacity={0} />
                                                </linearGradient>
                                            </defs>
                                            <XAxis dataKey="day" tick={{ fill: "#8888A8", fontSize: 11, fontFamily: "JetBrains Mono" }} axisLine={{ stroke: "#1E1E2E" }} tickLine={false} interval={4} />
                                            <YAxis tick={{ fill: "#8888A8", fontSize: 11, fontFamily: "JetBrains Mono" }} axisLine={false} tickLine={false} />
                                            <Tooltip contentStyle={{ background: "#12121A", border: "1px solid #1E1E2E", borderRadius: 8, fontSize: 12 }} labelStyle={{ color: "#8888A8" }} itemStyle={{ color: "#E8E8F0" }} />
                                            <Area dataKey="count" stroke="var(--c-accent)" fill="url(#g1)" strokeWidth={2} dot={false} />
                                        </AreaChart>
                                    </ResponsiveContainer>
                                </div>
                            )}
                        </section>

                        <section className="sm-card p-6">
                            <h2 className="text-title text-content mb-4">Search activity</h2>
                            {!useMocks ? (
                                <EmptyState
                                    testId="search-activity-unavailable"
                                    icon={LineChartIcon}
                                    headline="No search activity yet"
                                    description="This needs a /analytics/search-activity endpoint, which the backend does not expose yet."
                                />
                            ) : (
                                <>
                                    <div className="font-mono text-hero tabular-nums text-content">
                                        {searchSeries.reduce((a, b) => a + (b.searches || 0), 0)}
                                    </div>
                                    <div className="sm-micro-label mb-4">Searches · 14d</div>
                                    <div className="h-[100px]">
                                        <ResponsiveContainer width="100%" height="100%">
                                            <LineChart data={searchSeries}>
                                                <Line dataKey="searches" stroke="var(--c-accent)" strokeWidth={2} dot={false} />
                                            </LineChart>
                                        </ResponsiveContainer>
                                    </div>
                                </>
                            )}
                        </section>
                    </div>
                </TabsContent>

                {/* ─── CONTRIBUTION ─────────────────────────────────────── */}
                <TabsContent value="contribution" className="space-y-4 mt-0">
                    <section className="sm-card p-6">
                        <div className="flex items-start justify-between gap-6 mb-5">
                            <div>
                                <h2 className="text-title text-content">Contribution map</h2>
                                <p className="text-body text-content-secondary mt-0.5">
                                    Share of attributed memories per contributor
                                </p>
                            </div>
                            <div className="flex items-baseline gap-1.5 shrink-0">
                                {contribs === null ? (
                                    <Skeleton className="h-[34px] w-16" />
                                ) : (
                                    <>
                                        <span className="font-mono text-hero tabular-nums text-content">
                                            {contribs.length}
                                        </span>
                                        <span className="text-body-lg text-content-secondary">
                                            contributor{contribs.length === 1 ? "" : "s"}
                                        </span>
                                    </>
                                )}
                            </div>
                        </div>

                        {contribs === null ? (
                            <div className="space-y-3" role="status" aria-busy="true" aria-label="Loading contributors">
                                <Skeleton className="h-3 w-full rounded-full" />
                                {Array.from({ length: 3 }).map((_, i) => (
                                    <Skeleton key={i} className="h-tablerow w-full" />
                                ))}
                                <span className="sr-only">Loading contributors…</span>
                            </div>
                        ) : contribs.length === 0 ? (
                            <EmptyState
                                testId="contribution-empty"
                                icon={Users}
                                noun="contributors"
                                description="Once memories carry attribution, each person's share of the workspace appears here."
                            />
                        ) : (
                            <>
                                {/* §4.5's "By Type" card: a horizontal segmented
                                    bar showing proportion, with a dot legend —
                                    replacing the Treemap, whose per-contributor
                                    fills violate §2's single-accent rule. */}
                                <ContributionShareBar contribs={contribs} />

                                <table className="w-full mt-6">
                                    <thead>
                                        <tr className="text-left border-b border-hairline">
                                            <th className="sm-micro-label font-semibold pb-2">Contributor</th>
                                            <th className="sm-micro-label font-semibold pb-2 text-right">Memories</th>
                                            <th className="sm-micro-label font-semibold pb-2 w-[180px]">Avg share</th>
                                            <th className="sm-micro-label font-semibold pb-2 text-right">Last active</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {contribs.map((c) => (
                                            <tr
                                                key={c.id}
                                                data-testid={`contributor-row-${c.login}`}
                                                className="border-b border-hairline last:border-0 hover:bg-white/[0.02]"
                                            >
                                                <td className="py-3">
                                                    <div className="min-w-0">
                                                        <div className="text-body text-content truncate">{c.name}</div>
                                                        <div className="font-mono text-[10.5px] text-content-secondary truncate">
                                                            @{c.login}
                                                        </div>
                                                    </div>
                                                </td>
                                                <td className="py-3 text-right font-mono text-body text-content tabular-nums">
                                                    {(c.count ?? 0).toLocaleString()}
                                                </td>
                                                <td className="py-3">
                                                    {/* §4.5 — number and bar, one cell. */}
                                                    <InlineBar value={Math.round((c.score ?? 0) * 100)} width="96px" />
                                                </td>
                                                <td className="py-3 text-right font-mono text-[11px] text-content-secondary">
                                                    {c.last_active ? relativeTime(c.last_active) : "—"}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </>
                        )}
                    </section>
                </TabsContent>

                {/* ─── GAPS ─────────────────────────────────────────────── */}
                <TabsContent value="gaps" className="space-y-4 mt-0">
                    <div className="flex items-center gap-2" data-testid="gaps-filter-bar">
                        <Filter className="w-4 h-4 text-content-secondary" aria-hidden="true" />
                        {["ALL", "HIGH", "MEDIUM", "LOW"].map(l => (
                            <button
                                key={l}
                                data-testid={`gap-filter-${l.toLowerCase()}`}
                                onClick={() => setRiskFilter(l)}
                                aria-pressed={riskFilter === l}
                                className={`h-8 px-3 rounded-md font-mono text-[11px] transition-colors sm-focusable ${
                                    riskFilter === l
                                        ? "bg-brand/10 text-brand"
                                        : "text-content-secondary hover:text-content hover:bg-white/[0.03]"
                                }`}
                            >
                                {l}
                            </button>
                        ))}
                    </div>

                    {gapsRes.error ? (
                        <div className="sm-card">
                            <ErrorState error={gapsRes.error} onRetry={gapsRes.retry} testId="gaps-error" />
                        </div>
                    ) : gaps === null ? (
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4" role="status" aria-busy="true" aria-label="Loading knowledge gaps">
                            {Array.from({ length: 2 }).map((_, i) => (
                                <Skeleton key={i} className="h-[168px] w-full rounded-card" />
                            ))}
                            <span className="sr-only">Loading knowledge gaps…</span>
                        </div>
                    ) : filteredGaps.length === 0 ? (
                        <div className="sm-card">
                            <EmptyState
                                testId="gaps-empty"
                                icon={AlertCircle}
                                headline={
                                    gaps.length === 0
                                        ? "No knowledge gaps yet"
                                        : `No ${riskFilter.toLowerCase()}-risk gaps`
                                }
                                description={
                                    gaps.length === 0
                                        ? "Single-owner topics, stale memories and high-conflict areas will be flagged here as they emerge."
                                        : "Nothing at this risk level right now. Switch the filter to see the others."
                                }
                            />
                        </div>
                    ) : (
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                            {filteredGaps.map((g, i) => (
                                <div key={i} className="sm-card p-5" data-testid={`full-gap-card-${i}`}>
                                    <div className="flex items-center justify-between mb-3">
                                        <RiskBadge level={g.risk_level} />
                                        <span className="sm-micro-label">{g.gap_type}</span>
                                    </div>
                                    <p className="text-body text-content leading-relaxed mb-4">
                                        {g.description}
                                    </p>
                                    <div className="pt-4 border-t border-hairline space-y-2">
                                        <div className="font-mono text-[11px] text-content-secondary">
                                            {/* Field is `affected_memories`, not `affected_count`. */}
                                            {(g.affected_memories ?? 0).toLocaleString()} affected memories
                                        </div>
                                        {/* Field is `recommendation`, not `recommended_action`. */}
                                        {g.recommendation && (
                                            <p className="text-body text-content-secondary leading-snug">
                                                {g.recommendation}
                                            </p>
                                        )}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </TabsContent>
            </Tabs>
        </>
    );
}

/**
 * §4.5's segmented proportion bar plus its dot legend. One hue, stepped by
 * opacity through --c-data-1..4, so no contributor owns a colour.
 */
function ContributionShareBar({ contribs }) {
    const total = contribs.reduce((a, c) => a + (c.count || 0), 0) || 1;
    const shown = contribs.slice(0, 4);
    return (
        <div>
            <div
                className="flex w-full h-3 rounded-full overflow-hidden"
                style={{ background: "var(--c-data-track)" }}
                aria-hidden="true"
            >
                {shown.map((c, i) => (
                    <div
                        key={c.id}
                        style={{
                            width: `${((c.count || 0) / total) * 100}%`,
                            background: `var(--c-data-${Math.min(i + 1, 4)})`,
                        }}
                    />
                ))}
            </div>
            <div className="flex items-center gap-4 flex-wrap mt-3">
                {shown.map((c, i) => (
                    <div key={c.id} className="flex items-center gap-1.5">
                        <span
                            className="w-2 h-2 rounded-full shrink-0"
                            style={{ background: `var(--c-data-${Math.min(i + 1, 4)})` }}
                            aria-hidden="true"
                        />
                        <span className="font-mono text-[11px] text-content-secondary">
                            @{c.login}
                        </span>
                        <span className="font-mono text-[11px] text-content tabular-nums">
                            {Math.round(((c.count || 0) / total) * 100)}%
                        </span>
                    </div>
                ))}
            </div>
        </div>
    );
}
