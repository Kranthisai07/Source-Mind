import React, { useEffect, useState } from "react";
import { Brain, Users, TrendingUp, AlertCircle, ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";
import TopBar from "../components/layout/TopBar";
import MetricCard from "../components/widgets/MetricCard";
import HealthGauge from "../components/widgets/HealthGauge";
import RiskBadge from "../components/widgets/RiskBadge";
import api from "../lib/api";
import { relativeTime } from "../lib/format";

export default function Dashboard() {
    const [data, setData] = useState(null);
    const [gaps, setGaps] = useState(null);

    useEffect(() => {
        api.getAnalyticsOverview("ws_acme_platform").then(setData);
        api.getKnowledgeGaps("ws_acme_platform").then(r => setGaps(r.gaps));
    }, []);

    const subtitle = data
        ? `${data.total_memories.toLocaleString()} memories · ${data.total_contributors} contributors · health ${data.knowledge_health_score}/100`
        : "loading…";

    return (
        <>
            <TopBar
                title="Dashboard"
                subtitle={subtitle}
                actions={
                    <Link to="/memories">
                        <button data-testid="dashboard-browse-btn" className="h-9 px-3.5 rounded-lg bg-white/[0.04] hover:bg-white/[0.08] border border-sm-border text-[12.5px] font-medium text-sm-text transition-colors flex items-center gap-1.5">
                            Browse memories <ArrowRight className="w-3.5 h-3.5" />
                        </button>
                    </Link>
                }
            />
            <div className="flex-1 px-8 py-6 space-y-5">
                {/* Row 1 — metrics */}
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                    <MetricCard
                        testId="metric-total-memories"
                        icon={Brain}
                        label="Total Memories"
                        value={data?.total_memories.toLocaleString() ?? "—"}
                        delta={<span>Across {data?.total_contributors ?? "—"} contributors</span>}
                        accent="#4F7EFF"
                    />
                    <MetricCard
                        testId="metric-contributors"
                        icon={Users}
                        label="Active Contributors"
                        value={data?.total_contributors ?? "—"}
                        delta={<span className="text-sm-green">● All active this week</span>}
                        accent="#A78BFA"
                    />
                    <MetricCard
                        testId="metric-new-this-month"
                        icon={TrendingUp}
                        label="New This Month"
                        value={data?.memories_created_last_30_days ?? "—"}
                        delta={<span className="text-sm-green">↑ 18% vs. last 30d</span>}
                        accent="#34D399"
                    />
                    <MetricCard
                        testId="metric-open-conflicts"
                        icon={AlertCircle}
                        label="Open Conflicts"
                        value={data?.open_conflicts ?? "—"}
                        delta={
                            data?.open_conflicts > 0
                                ? <Link to="/conflicts" className="text-sm-amber hover:underline">Review now →</Link>
                                : <span className="text-sm-text-secondary">All clear</span>
                        }
                        accent={data?.open_conflicts > 0 ? "#F59E0B" : "#34D399"}
                    />
                </div>

                {/* Row 2 — health + activity (3:2) */}
                <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
                    <section className="sm-card p-6 lg:col-span-3">
                        <div className="flex items-center justify-between mb-4">
                            <div>
                                <h2 className="text-[15px] font-semibold text-sm-text">Knowledge Health Score</h2>
                                <p className="text-[12px] text-sm-text-secondary mt-0.5">Weighted composite · refreshed hourly</p>
                            </div>
                            <Link to="/analytics" className="text-[12px] text-sm-blue hover:underline">View details →</Link>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-[auto_1fr] gap-8 items-center">
                            <HealthGauge score={data?.knowledge_health_score ?? 0} size={180} />
                            <div className="space-y-3.5 min-w-0">
                                <HealthBar label="Coverage"            weight="30%" value={data?.health_breakdown.coverage ?? 0}             color="#4F7EFF" />
                                <HealthBar label="Freshness"           weight="30%" value={data?.health_breakdown.freshness ?? 0}            color="#A78BFA" />
                                <HealthBar label="Conflict Ratio"      weight="25%" value={data?.health_breakdown.conflict_ratio ?? 0}       color="#34D399" />
                                <HealthBar label="Attribution"         weight="15%" value={data?.health_breakdown.attribution_coverage ?? 0} color="#F59E0B" />
                            </div>
                        </div>
                    </section>

                    <section className="sm-card p-6 lg:col-span-2">
                        <div className="flex items-center justify-between mb-4">
                            <h2 className="text-[15px] font-semibold text-sm-text">Recent Activity</h2>
                            <span className="font-mono text-[10.5px] text-sm-text-secondary">LIVE</span>
                        </div>
                        <div className="space-y-3 max-h-[320px] overflow-y-auto pr-1">
                            {(data?.recent_activity ?? []).map((a, i) => (
                                <div key={i} data-testid={`activity-${i}`} className="flex items-start gap-3 fade-in-up" style={{ animationDelay: `${i * 40}ms` }}>
                                    <div className="w-2 h-2 rounded-full mt-1.5 shrink-0" style={{ background: a.color, boxShadow: `0 0 8px ${a.color}` }} />
                                    <div className="flex-1 min-w-0">
                                        <p className="text-[12.5px] text-sm-text leading-snug">{a.description}</p>
                                        <p className="font-mono text-[10.5px] text-sm-text-secondary mt-0.5">{relativeTime(a.timestamp)}</p>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </section>
                </div>

                {/* Row 3 — top contributors + knowledge gaps */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    <section className="sm-card p-6">
                        <div className="flex items-center justify-between mb-5">
                            <h2 className="text-[15px] font-semibold text-sm-text">Top Contributors</h2>
                            <Link to="/analytics" className="text-[12px] text-sm-blue hover:underline">Contribution map →</Link>
                        </div>
                        <div className="space-y-2.5">
                            {(data?.top_contributors ?? []).map((c, i) => {
                                const max = data.top_contributors[0].count;
                                const pct = (c.count / max) * 100;
                                return (
                                    <div key={c.name} className="flex items-center gap-3" data-testid={`top-contributor-${i}`}>
                                        <span className="font-mono text-[11px] text-sm-text-secondary w-6 text-right shrink-0">{i + 1}</span>
                                        <span className="text-[12.5px] text-sm-text w-36 truncate font-mono">@{c.name}</span>
                                        <div className="flex-1 h-6 rounded-md bg-sm-border/50 relative overflow-hidden">
                                            <div
                                                className="absolute inset-y-0 left-0 bar-grow rounded-md flex items-center pr-2 justify-end"
                                                style={{
                                                    width: `${pct}%`,
                                                    background: `linear-gradient(90deg, ${c.color}50, ${c.color})`,
                                                    animationDelay: `${i * 80}ms`,
                                                }}
                                            >
                                                <span className="font-mono text-[10.5px] text-white font-semibold">{c.count}</span>
                                            </div>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </section>

                    <section className="sm-card p-6">
                        <div className="flex items-center justify-between mb-5">
                            <h2 className="text-[15px] font-semibold text-sm-text">Knowledge Gaps</h2>
                            <Link to="/analytics" className="text-[12px] text-sm-blue hover:underline" data-testid="dashboard-view-all-gaps">View all →</Link>
                        </div>
                        <div className="space-y-2.5">
                            {(gaps ?? []).slice(0, 3).map((g, i) => (
                                <div key={i} data-testid={`gap-card-${i}`} className="rounded-lg border border-sm-border bg-sm-bg/40 p-4 hover:border-sm-border-hover transition-colors">
                                    <div className="flex items-center justify-between mb-2">
                                        <RiskBadge level={g.risk_level} />
                                        <span className="font-mono text-[10.5px] text-sm-text-secondary">{g.gap_type}</span>
                                    </div>
                                    <p className="text-[12.5px] text-sm-text leading-snug">{g.description}</p>
                                    <p className="font-mono text-[10.5px] text-sm-text-secondary mt-2">{g.affected_count} affected memories</p>
                                </div>
                            ))}
                        </div>
                    </section>
                </div>
            </div>
        </>
    );
}

function HealthBar({ label, weight, value, color }) {
    return (
        <div>
            <div className="flex items-center justify-between mb-1.5">
                <span className="text-[12px] text-sm-text">
                    {label}
                    <span className="font-mono text-[10.5px] text-sm-text-muted ml-1.5">({weight})</span>
                </span>
                <span className="font-mono text-[11px] text-sm-text">{value}%</span>
            </div>
            <div className="h-1.5 rounded-full bg-sm-border/50 overflow-hidden">
                <div
                    className="h-full rounded-full bar-grow"
                    style={{ width: `${value}%`, background: color }}
                />
            </div>
        </div>
    );
}
