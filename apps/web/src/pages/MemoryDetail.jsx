import React from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Edit3, Trash2, AlertTriangle, Share2 } from "lucide-react";
import { PieChart, Pie, Cell, ResponsiveContainer } from "recharts";
import TopBar from "../components/layout/TopBar";
import ContributorAvatar from "../components/widgets/ContributorAvatar";
import Markdown from "../components/widgets/Markdown";
import { PageLoadError } from "../components/widgets/PageLoadState";
import { Button } from "../components/ui/button";
import useApiResource from "../hooks/useApiResource";
import api from "../lib/api";
import { relativeTime, formatDate } from "../lib/format";

export default function MemoryDetail() {
    const { id } = useParams();
    const navigate = useNavigate();
    const memoryResource = useApiResource(() => api.getMemory(id), { resetKey: id });
    const versionsResource = useApiResource(() => api.getMemoryVersions(id), { resetKey: id });
    const mem = memoryResource.data;
    const versions = Array.isArray(versionsResource.data?.versions) ? versionsResource.data.versions : [];

    if (memoryResource.error) {
        return <><TopBar title="Memory" subtitle="This memory could not be loaded" /><div className="flex-1 px-8 py-6"><PageLoadError error={memoryResource.error} onRetry={memoryResource.retry} testId="memory-detail-error" /></div></>;
    }

    if (memoryResource.loading || !mem) {
        return (
            <>
                <TopBar title="Memory" subtitle="loading…" />
                <div className="flex-1 px-8 py-6 grid grid-cols-3 gap-4">
                    <div className="sm-card col-span-2 h-[500px] shimmer" />
                    <div className="sm-card h-[500px] shimmer" />
                </div>
            </>
        );
    }

    // realApi normalises ContributionBreakdown[] into this shape and returns
    // [] when the backend reports none; mockApi already produces it. Both can
    // still be empty, so nothing below may index without checking.
    const attribution = Array.isArray(mem.attribution) ? mem.attribution : [];
    const primary =
        attribution.find(a => a.is_primary) ||
        attribution[0] ||
        null;
    const primaryPct = primary
        ? Math.round((primary.percentage ?? (primary.score ?? 0) * 100))
        : null;
    const tags = Array.isArray(mem.tags) ? mem.tags : [];
    const memoryId = mem.id || mem.memory_id || id;
    const subtitle = [
        mem.category,
        mem.version != null ? `v${mem.version}` : null,
        mem.created_at ? `created ${relativeTime(mem.created_at)}` : null,
    ].filter(Boolean).join(" · ") || "No metadata recorded";

    return (
        <>
            <TopBar
                title={<span className="font-mono text-[14px] text-sm-text-secondary">{memoryId}</span>}
                subtitle={subtitle}
                actions={
                    <>
                        <Button variant="ghost" size="sm" onClick={() => navigate(-1)} className="text-sm-text-secondary" data-testid="mem-back">
                            <ArrowLeft className="w-4 h-4" /> Back
                        </Button>
                        <Button variant="outline" size="sm" disabled data-testid="mem-edit" title="Editing a memory isn't available on this screen yet" className="bg-white/[0.04] border-sm-border text-sm-text disabled:opacity-40 disabled:cursor-not-allowed"><Edit3 className="w-3.5 h-3.5" /> Edit</Button>
                        <Button variant="outline" size="sm" disabled data-testid="mem-share" title="Sharing a memory isn't available on this screen yet" className="bg-white/[0.04] border-sm-border text-sm-text disabled:opacity-40 disabled:cursor-not-allowed"><Share2 className="w-3.5 h-3.5" /> Share</Button>
                        <Button variant="outline" size="sm" disabled data-testid="mem-delete" title="Deleting a memory isn't available on this screen yet" className="bg-white/[0.04] border-sm-border text-sm-red hover:text-sm-red disabled:opacity-40 disabled:cursor-not-allowed"><Trash2 className="w-3.5 h-3.5" /> Delete</Button>
                    </>
                }
            />
            <div className="flex-1 px-8 py-6 grid grid-cols-1 lg:grid-cols-3 gap-4">
                {/* Main content */}
                <section className="sm-card p-8 lg:col-span-2">
                    <div className="flex items-center gap-1.5 flex-wrap mb-5">
                        {tags.map(t => (
                            <span key={t} className="font-mono text-[10.5px] px-2 py-0.5 rounded-md border border-sm-border text-sm-text-secondary">{t}</span>
                        ))}
                        {mem.category && (
                            <span className="font-mono text-[10.5px] px-2 py-0.5 rounded-md border border-sm-blue/30 bg-sm-blue/10 text-sm-blue ml-auto">
                                {mem.category}
                            </span>
                        )}
                    </div>
                    <article className="prose prose-invert max-w-none">
                        <Markdown>{mem.content}</Markdown>
                    </article>

                    <div className="mt-8 pt-6 border-t border-sm-border flex items-center justify-between">
                        <div className="flex items-center gap-3">
                            <ContributorAvatar contributor={primary} size={32} />
                            <div>
                                <div className="text-[13px] font-medium text-sm-text">
                                    {primary?.name || primary?.author || "Unattributed"}
                                </div>
                                <div className="font-mono text-[11px] text-sm-text-secondary">
                                    {primary ? "primary author" : "no attribution recorded"}
                                </div>
                            </div>
                        </div>
                        <div className="text-right">
                            <div className="font-mono text-[11px] text-sm-text-secondary">created</div>
                            <div className="font-mono text-[12px] text-sm-text">{formatDate(mem.created_at)}</div>
                        </div>
                    </div>

                    {/* Attribution details */}
                    <div className="mt-8">
                        <h3 className="text-[14px] font-semibold text-sm-text mb-4">Attribution Breakdown</h3>
                        <div className="grid grid-cols-1 md:grid-cols-[180px_1fr] gap-6 items-center">
                            <div className="w-[180px] h-[180px] relative">
                                <ResponsiveContainer width="100%" height="100%">
                                    <PieChart>
                                        <Pie
                                            data={attribution}
                                            dataKey="score"
                                            nameKey="author"
                                            innerRadius={50}
                                            outerRadius={80}
                                            startAngle={90}
                                            endAngle={-270}
                                            stroke="#12121A"
                                            strokeWidth={2}
                                        >
                                            {attribution.map((a, i) => (
                                                <Cell key={i} fill={a.color} />
                                            ))}
                                        </Pie>
                                    </PieChart>
                                </ResponsiveContainer>
                                <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                                    <span className="font-mono text-[22px] font-semibold text-sm-text">{primaryPct === null ? "—" : `${primaryPct}%`}</span>
                                    <span className="text-[10.5px] text-sm-text-secondary font-mono uppercase tracking-wider">primary</span>
                                </div>
                            </div>

                            <table className="w-full text-[12.5px]">
                                <thead>
                                    <tr className="text-sm-text-secondary text-left">
                                        <th className="font-medium py-2">Contributor</th>
                                        <th className="font-medium py-2 text-right">Score</th>
                                        <th className="font-medium py-2 font-mono text-[10.5px] text-right">S1 · S2 · S3 · S4 · S5</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {attribution.map((a, i) => (
                                        <tr key={i} className="border-t border-sm-border">
                                            <td className="py-2.5">
                                                <div className="flex items-center gap-2">
                                                    <span className="w-2.5 h-2.5 rounded-full" style={{ background: a.color }} />
                                                    <span className="font-mono text-sm-text">{a.author}</span>
                                                </div>
                                            </td>
                                            <td className="py-2.5 text-right font-mono text-sm-text">{Math.round((a.percentage ?? (a.score ?? 0) * 100))}%</td>
                                            <td className="py-2.5 text-right font-mono text-[10.5px] text-sm-text-secondary">
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
                        </div>
                    </div>
                </section>

                {/* Version timeline */}
                <aside className="sm-card p-6">
                    <h3 className="text-[14px] font-semibold text-sm-text mb-5">Version Timeline</h3>
                    {versionsResource.error ? (
                        <PageLoadError error={versionsResource.error} onRetry={versionsResource.retry} testId="memory-versions-error" compact />
                    ) : versionsResource.loading ? (
                        <p data-testid="memory-versions-loading" role="status" className="text-[12.5px] text-sm-text-secondary">Loading version history…</p>
                    ) : versions.length === 0 ? (
                        <p className="text-[12.5px] text-sm-text-secondary">No version history is available.</p>
                    ) : <ol className="relative border-l-2 border-sm-border ml-2 space-y-5">
                        {versions.map((version, index) => (
                            <li key={version.id || `${version.version}-${index}`} className="pl-5 relative" data-testid={`memory-version-${version.version}`}>
                                <span className="absolute -left-[9px] top-1 w-4 h-4 rounded-full border-2 border-sm-bg bg-sm-blue" />
                                <div className="flex flex-wrap items-center gap-2 mb-1">
                                    <span className="font-mono text-[11px] text-sm-blue">v{version.version}</span>
                                    <span className={`font-mono text-[9.5px] uppercase tracking-wider ${version.is_current ? "text-sm-green" : "text-sm-text-muted"}`}>
                                        {version.is_current ? "Current" : "Historical"}
                                    </span>
                                    <time
                                        dateTime={version.created_at || undefined}
                                        className="font-mono text-[10.5px] text-sm-text-secondary"
                                    >
                                        {version.created_at ? formatDate(version.created_at) : "Date unavailable"}
                                    </time>
                                </div>
                                <p className="text-[12.5px] text-sm-text leading-relaxed whitespace-pre-wrap">{version.content || "No content recorded."}</p>
                            </li>
                        ))}
                    </ol>}

                    <Button variant="outline" size="sm" className="w-full mt-6 bg-white/[0.03] border-sm-border text-sm-amber hover:bg-sm-amber/10" onClick={() => navigate("/conflicts")}>
                        <AlertTriangle className="w-3.5 h-3.5" /> View Conflicts
                    </Button>
                </aside>
            </div>
        </>
    );
}
