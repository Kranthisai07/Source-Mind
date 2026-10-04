import React, { useState } from "react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis, LineChart, Line, Treemap } from "recharts";
import { Filter } from "lucide-react";
import TopBar from "../components/layout/TopBar";
import HealthGauge from "../components/widgets/HealthGauge";
import RiskBadge from "../components/widgets/RiskBadge";
import { PageLoadError, PageLoading } from "../components/widgets/PageLoadState";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../components/ui/tabs";
import useApiResource from "../hooks/useApiResource";
import api, { useMocks } from "../lib/api";
import { relativeTime } from "../lib/format";

export default function Analytics() {
    const [riskFilter, setRiskFilter] = useState("ALL");

    const resource = useApiResource(async () => {
        const [overviewResult, gapsResult, contributorsResult, memorySeriesResult, searchActivityResult] = await Promise.allSettled([
            api.getAnalyticsOverview(),
            api.getKnowledgeGaps(),
            api.listContributors(),
            useMocks ? api.getMemoriesOverTime() : Promise.resolve({ series: [] }),
            useMocks ? api.getSearchActivity() : Promise.resolve({ series: [] }),
        ]);
        return {
            overview: settledValue(overviewResult, null),
            overviewError: settledError(overviewResult),
            gaps: settledValue(gapsResult, { gaps: [] }).gaps,
            gapsError: settledError(gapsResult),
            contributors: settledValue(contributorsResult, { contributors: [] }).contributors,
            contributorsError: settledError(contributorsResult),
            series: settledValue(memorySeriesResult, { series: [] }).series,
            seriesError: settledError(memorySeriesResult),
            searchSeries: settledValue(searchActivityResult, { series: [] }).series,
            searchSeriesError: settledError(searchActivityResult),
        };
    });
    const overview = resource.data?.overview ?? null;
    const overviewError = resource.data?.overviewError ?? null;
    const gaps = resource.data?.gaps ?? [];
    const gapsError = resource.data?.gapsError ?? null;
    const contribs = resource.data?.contributors ?? [];
    const contributorsError = resource.data?.contributorsError ?? null;
    const series = resource.data?.series ?? [];
    const seriesError = resource.data?.seriesError ?? null;
    const searchSeries = resource.data?.searchSeries ?? [];
    const searchSeriesError = resource.data?.searchSeriesError ?? null;

    const filteredGaps = gaps.filter(g => riskFilter === "ALL" || String(g.risk_level || "").toUpperCase() === riskFilter);

    if (resource.error) {
        return <><TopBar title="Analytics" subtitle="Workspace analytics unavailable" /><div className="flex-1 px-8 py-6"><PageLoadError error={resource.error} onRetry={resource.retry} testId="analytics-error" /></div></>;
    }

    if (resource.loading && !resource.data) {
        return <><TopBar title="Analytics" subtitle="Loading workspace analytics…" /><div className="flex-1 px-8 py-6"><PageLoading label="Loading analytics" testId="analytics-loading" /></div></>;
    }

    return (
        <>
            <TopBar
                title="Analytics"
                subtitle="Deep dive into workspace knowledge health"
            />
            <div className="flex-1 px-8 py-6">
                <Tabs defaultValue="overview" className="w-full">
                    <TabsList className="bg-sm-surface border border-sm-border p-1 h-10 mb-6" data-testid="analytics-tabs">
                        <TabsTrigger value="overview"     data-testid="tab-overview"    className="data-[state=active]:bg-sm-blue/15 data-[state=active]:text-sm-blue text-sm-text-secondary">Overview</TabsTrigger>
                        <TabsTrigger value="contribution" data-testid="tab-contribution" className="data-[state=active]:bg-sm-blue/15 data-[state=active]:text-sm-blue text-sm-text-secondary">Contribution Map</TabsTrigger>
                        <TabsTrigger value="gaps"         data-testid="tab-gaps"         className="data-[state=active]:bg-sm-blue/15 data-[state=active]:text-sm-blue text-sm-text-secondary">Knowledge Gaps</TabsTrigger>
                    </TabsList>

                    {/* OVERVIEW */}
                    <TabsContent value="overview" className="space-y-5 mt-0">
                        {overviewError ? (
                            <PageLoadError error={panelError("Health overview unavailable", overviewError)} onRetry={resource.retry} testId="analytics-overview-error" />
                        ) : <div className="grid grid-cols-1 lg:grid-cols-[300px_1fr] gap-4">
                            <section className="sm-card p-6 flex flex-col items-center">
                                    <HealthGauge score={overview?.knowledge_health_score ?? 0} size={220} label="Health Score" />
                                    <div className="mt-6 text-center">
                                        <div className="font-mono text-[11px] text-sm-text-secondary mb-1">CURRENT SCORE</div>
                                        <div className="text-sm-text-secondary font-mono text-[12px]">No historical comparison available</div>
                                </div>
                            </section>
                            <section className="sm-card p-6">
                                <h3 className="text-[15px] font-semibold text-sm-text mb-5">Health Breakdown</h3>
                                <div className="space-y-4">
                                    <BigBar label="Coverage"     weight="30%" value={overview?.health_breakdown.coverage ?? 0}             color="#4F7EFF" desc="What % of your codebase/team activity is reflected in memories" />
                                    <BigBar label="Freshness"    weight="30%" value={overview?.health_breakdown.freshness ?? 0}            color="#A78BFA" desc="How recently memories have been updated or reviewed" />
                                    <BigBar label="Conflict Ratio" weight="25%" value={overview?.health_breakdown.conflict_ratio ?? 0}       color="#34D399" desc="Inverse of unresolved conflicts per active memory" />
                                    <BigBar label="Attribution"  weight="15%" value={overview?.health_breakdown.attribution_coverage ?? 0} color="#F59E0B" desc="% of memories with high-confidence author attribution" />
                                </div>
                            </section>
                        </div>}

                        {useMocks && (seriesError || searchSeriesError) ? (
                            <PageLoadError error={panelError("Historical analytics unavailable", seriesError || searchSeriesError)} onRetry={resource.retry} testId="analytics-series-error" />
                        ) : useMocks ? <div className="grid grid-cols-1 lg:grid-cols-[1fr_280px] gap-4">
                            <section className="sm-card p-6">
                                <div className="flex items-center justify-between mb-3">
                                    <h3 className="text-[15px] font-semibold text-sm-text">Memories Over Time</h3>
                                    <span className="font-mono text-[11px] text-sm-text-secondary">LAST 30 DAYS</span>
                                </div>
                                <div className="h-[220px]">
                                    <ResponsiveContainer width="100%" height="100%">
                                        <AreaChart data={series} margin={{ top: 5, right: 10, bottom: 0, left: -20 }}>
                                            <defs>
                                                <linearGradient id="g1" x1="0" y1="0" x2="0" y2="1">
                                                    <stop offset="0%" stopColor="#4F7EFF" stopOpacity={0.5} />
                                                    <stop offset="100%" stopColor="#4F7EFF" stopOpacity={0} />
                                                </linearGradient>
                                            </defs>
                                            <XAxis dataKey="day" tick={{ fill: "#8888A8", fontSize: 11, fontFamily: "JetBrains Mono" }} axisLine={{ stroke: "#1E1E2E" }} tickLine={false} interval={4} />
                                            <YAxis tick={{ fill: "#8888A8", fontSize: 11, fontFamily: "JetBrains Mono" }} axisLine={false} tickLine={false} />
                                            <Tooltip contentStyle={{ background: "#12121A", border: "1px solid #1E1E2E", borderRadius: 8, fontSize: 12 }} labelStyle={{ color: "#8888A8" }} itemStyle={{ color: "#E8E8F0" }} />
                                            <Area dataKey="count" stroke="#4F7EFF" fill="url(#g1)" strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: "#4F7EFF", fill: "#0A0A0F", strokeWidth: 2 }} />
                                        </AreaChart>
                                    </ResponsiveContainer>
                                </div>
                            </section>
                            <section className="sm-card p-6">
                                <h3 className="text-[14px] font-semibold text-sm-text mb-2">Search Activity</h3>
                                <div className="font-mono text-[28px] text-sm-text font-semibold">{searchSeries.reduce((a, b) => a + b.searches, 0)}</div>
                                <div className="font-mono text-[11px] text-sm-text-secondary mb-4">searches · 14d</div>
                                <div className="h-[100px]">
                                    <ResponsiveContainer width="100%" height="100%">
                                        <LineChart data={searchSeries}>
                                            <Line dataKey="searches" stroke="#34D399" strokeWidth={2} dot={false} />
                                        </LineChart>
                                    </ResponsiveContainer>
                                </div>
                            </section>
                        </div> : (
                            <div data-testid="analytics-series-unavailable" className="sm-card p-6">
                                <h3 className="text-[14px] font-semibold text-sm-text">Historical activity charts</h3>
                                <p className="mt-1 text-[12.5px] text-sm-text-secondary">
                                    Time-series analytics are not exposed by the production API yet.
                                </p>
                            </div>
                        )}
                    </TabsContent>

                    {/* CONTRIBUTION */}
                    <TabsContent value="contribution" className="space-y-5 mt-0">
                        {contributorsError ? (
                            <PageLoadError error={panelError("Contribution data unavailable", contributorsError)} onRetry={resource.retry} testId="analytics-contributors-error" />
                        ) : <>
                        <section className="sm-card p-6">
                            <h3 className="text-[15px] font-semibold text-sm-text mb-4">Contribution Map</h3>
                            <p className="text-[12.5px] text-sm-text-secondary mb-5">Tile size = memory count · colors distinguish contributors</p>
                            <div className="h-[340px]">
                                <ResponsiveContainer width="100%" height="100%">
                                    <Treemap
                                        data={contribs.map(c => ({
                                            name: c.login,
                                            size: c.count,
                                            fill: c.avatarColor,
                                        }))}
                                        dataKey="size"
                                        stroke="#0A0A0F"
                                        content={<TreemapNode />}
                                    />
                                </ResponsiveContainer>
                            </div>
                        </section>

                        <section className="sm-card p-6">
                            <h3 className="text-[14px] font-semibold text-sm-text mb-4">Contributor Breakdown</h3>
                            <table className="w-full text-[12.5px]">
                                <thead>
                                    <tr className="text-left font-mono text-[10.5px] uppercase tracking-wider text-sm-text-secondary border-b border-sm-border">
                                        <th className="py-2.5">Contributor</th>
                                        <th className="py-2.5 text-right">Memories</th>
                                        <th className="py-2.5 text-right">Avg Score</th>
                                        <th className="py-2.5 text-right">Last Active</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {contribs.map((c) => (
                                        <tr key={c.id} className="border-b border-sm-border/50 hover:bg-white/[0.02]">
                                            <td className="py-3">
                                                <div className="flex items-center gap-2">
                                                    <span className="w-2.5 h-2.5 rounded-full" style={{ background: c.avatarColor }} />
                                                    <span className="font-mono text-sm-text">@{c.login}</span>
                                                </div>
                                            </td>
                                            <td className="py-3 text-right font-mono">{c.count}</td>
                                            <td className="py-3 text-right font-mono text-sm-text">{c.score.toFixed(2)}</td>
                                            <td className="py-3 text-right font-mono text-sm-text-secondary">{c.last_active}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </section>
                        </>}
                    </TabsContent>

                    {/* GAPS */}
                    <TabsContent value="gaps" className="space-y-5 mt-0">
                        {gapsError ? (
                            <PageLoadError error={panelError("Knowledge gaps unavailable", gapsError)} onRetry={resource.retry} testId="analytics-gaps-error" />
                        ) : <>
                        <div className="flex items-center gap-2" data-testid="gaps-filter-bar">
                            <Filter className="w-4 h-4 text-sm-text-secondary" />
                            {["ALL", "HIGH", "MEDIUM", "LOW"].map(l => (
                                <button
                                    key={l}
                                    data-testid={`gap-filter-${l.toLowerCase()}`}
                                    onClick={() => setRiskFilter(l)}
                                    className={`h-8 px-3 rounded-md font-mono text-[11px] tracking-wider transition-colors ${
                                        riskFilter === l
                                            ? "bg-sm-blue/15 text-sm-blue border border-sm-blue/30"
                                            : "bg-white/[0.03] text-sm-text-secondary border border-sm-border hover:text-sm-text"
                                    }`}
                                >
                                    {l}
                                </button>
                            ))}
                        </div>

                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                            {filteredGaps.map((g, i) => (
                                <div key={i} className="sm-card p-5" data-testid={`full-gap-card-${i}`}>
                                    <div className="flex items-center justify-between mb-3">
                                        <RiskBadge level={g.risk_level} />
                                        <span className="font-mono text-[11px] text-sm-text-secondary">{g.gap_type}</span>
                                    </div>
                                    <p className="text-[13px] text-sm-text leading-relaxed mb-4">{g.description}</p>
                                    <div className="pt-4 border-t border-sm-border flex items-center justify-between">
                                        <div className="font-mono text-[11px] text-sm-text-secondary">{g.affected_count} affected memories</div>
                                        <div className="text-[12px] text-sm-blue">{g.recommended_action}</div>
                                    </div>
                                </div>
                            ))}
                        </div>
                        </>}
                    </TabsContent>
                </Tabs>
            </div>
        </>
    );
}

function BigBar({ label, weight, value, color, desc }) {
    return (
        <div>
            <div className="flex items-baseline justify-between mb-1">
                <div>
                    <span className="text-[13px] text-sm-text font-medium">{label}</span>
                    <span className="font-mono text-[11px] text-sm-text-muted ml-2">weight {weight}</span>
                </div>
                <span className="font-mono text-[14px] text-sm-text font-semibold">{value}%</span>
            </div>
            <p className="text-[11.5px] text-sm-text-secondary mb-2">{desc}</p>
            <div className="h-2 rounded-full bg-sm-border/50 overflow-hidden">
                <div className="h-full rounded-full bar-grow" style={{ width: `${value}%`, background: color }} />
            </div>
        </div>
    );
}

export function TreemapNode({ x, y, width, height, name, fill }) {
    if (width < 20 || height < 20) return null;
    return (
        <g>
            <rect x={x} y={y} width={width} height={height} fill={fill} fillOpacity={0.18} stroke="var(--sm-bg)" strokeWidth={2} />
            <rect x={x} y={y} width={width} height={4} fill={fill} />
            {width > 80 && height > 40 && (
                <text data-testid="treemap-label" x={x + 10} y={y + 22} fill="var(--sm-text)" fontSize={12} fontFamily="JetBrains Mono" fontWeight={600}>
                    @{name}
                </text>
            )}
        </g>
    );
}

function settledValue(result, fallback) {
    return result.status === "fulfilled" ? result.value : fallback;
}

function settledError(result) {
    return result.status === "rejected" ? result.reason : null;
}

function panelError(title, error) {
    return {
        title,
        detail: error?.message || "This analytics panel could not be loaded.",
        retryable: ![401, 403, 404].includes(error?.status),
    };
}
