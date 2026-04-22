import React, { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Edit3, Trash2, AlertTriangle, Share2 } from "lucide-react";
import { PieChart, Pie, Cell, ResponsiveContainer } from "recharts";
import TopBar from "../components/layout/TopBar";
import ContributorAvatar from "../components/widgets/ContributorAvatar";
import Markdown from "../components/widgets/Markdown";
import { Button } from "../components/ui/button";
import api from "../lib/api";
import { relativeTime, formatDate } from "../lib/format";
import { CONTRIBUTORS } from "../lib/mockData";

export default function MemoryDetail() {
    const { id } = useParams();
    const navigate = useNavigate();
    const [mem, setMem] = useState(null);

    useEffect(() => {
        api.getMemory(id).then(setMem);
    }, [id]);

    if (!mem) {
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

    const primary = CONTRIBUTORS.find(c => c.login === mem.attribution[0].author);

    return (
        <>
            <TopBar
                title={<span className="font-mono text-[14px] text-sm-text-secondary">{mem.memory_id}</span>}
                subtitle={`${mem.category} · v${mem.version} · created ${relativeTime(mem.created_at)}`}
                actions={
                    <>
                        <Button variant="ghost" size="sm" onClick={() => navigate(-1)} className="text-sm-text-secondary" data-testid="mem-back">
                            <ArrowLeft className="w-4 h-4" /> Back
                        </Button>
                        <Button variant="outline" size="sm" className="bg-white/[0.04] border-sm-border text-sm-text"><Edit3 className="w-3.5 h-3.5" /> Edit</Button>
                        <Button variant="outline" size="sm" className="bg-white/[0.04] border-sm-border text-sm-text"><Share2 className="w-3.5 h-3.5" /> Share</Button>
                        <Button variant="outline" size="sm" className="bg-white/[0.04] border-sm-border text-sm-red hover:text-sm-red"><Trash2 className="w-3.5 h-3.5" /> Delete</Button>
                    </>
                }
            />
            <div className="flex-1 px-8 py-6 grid grid-cols-1 lg:grid-cols-3 gap-4">
                {/* Main content */}
                <section className="sm-card p-8 lg:col-span-2">
                    <div className="flex items-center gap-1.5 flex-wrap mb-5">
                        {mem.tags.map(t => (
                            <span key={t} className="font-mono text-[10.5px] px-2 py-0.5 rounded-md border border-sm-border text-sm-text-secondary">{t}</span>
                        ))}
                        <span className="font-mono text-[10.5px] px-2 py-0.5 rounded-md border border-sm-blue/30 bg-sm-blue/10 text-sm-blue ml-auto">
                            {mem.category}
                        </span>
                    </div>
                    <article className="prose prose-invert max-w-none">
                        <Markdown>{mem.content}</Markdown>
                    </article>

                    <div className="mt-8 pt-6 border-t border-sm-border flex items-center justify-between">
                        <div className="flex items-center gap-3">
                            <ContributorAvatar contributor={primary} size={32} />
                            <div>
                                <div className="text-[13px] font-medium text-sm-text">{primary?.name}</div>
                                <div className="font-mono text-[11px] text-sm-text-secondary">@{primary?.login} · primary author</div>
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
                                            data={mem.attribution}
                                            dataKey="score"
                                            nameKey="author"
                                            innerRadius={50}
                                            outerRadius={80}
                                            startAngle={90}
                                            endAngle={-270}
                                            stroke="#12121A"
                                            strokeWidth={2}
                                        >
                                            {mem.attribution.map((a, i) => (
                                                <Cell key={i} fill={a.color} />
                                            ))}
                                        </Pie>
                                    </PieChart>
                                </ResponsiveContainer>
                                <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                                    <span className="font-mono text-[22px] font-semibold text-sm-text">{Math.round(mem.attribution[0].score * 100)}%</span>
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
                                    {mem.attribution.map((a, i) => (
                                        <tr key={i} className="border-t border-sm-border">
                                            <td className="py-2.5">
                                                <div className="flex items-center gap-2">
                                                    <span className="w-2.5 h-2.5 rounded-full" style={{ background: a.color }} />
                                                    <span className="font-mono text-sm-text">@{a.author}</span>
                                                </div>
                                            </td>
                                            <td className="py-2.5 text-right font-mono text-sm-text">{Math.round(a.score * 100)}%</td>
                                            <td className="py-2.5 text-right font-mono text-[10.5px] text-sm-text-secondary">
                                                {Object.values(a.signals).map(v => v.toFixed(2)).join(" · ")}
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
                    <ol className="relative border-l-2 border-sm-border ml-2 space-y-5">
                        {mem.versions.map((v, i) => {
                            const editor = CONTRIBUTORS.find(c => c.login === v.editor) || { name: v.editor, login: v.editor, avatarColor: "#4F7EFF" };
                            return (
                                <li key={i} className="pl-5 relative">
                                    <span className="absolute -left-[9px] top-1 w-4 h-4 rounded-full border-2 border-sm-bg" style={{ background: editor.avatarColor }} />
                                    <div className="flex items-center gap-2 mb-1">
                                        <span className="font-mono text-[11px] text-sm-blue">v{v.v}</span>
                                        <span className="font-mono text-[10.5px] text-sm-text-secondary">{relativeTime(v.at)}</span>
                                    </div>
                                    <div className="text-[12.5px] text-sm-text">@{editor.login}</div>
                                    <div className="font-mono text-[11px] text-sm-text-secondary mt-0.5">{v.summary}</div>
                                </li>
                            );
                        })}
                    </ol>

                    <Button variant="outline" size="sm" className="w-full mt-6 bg-white/[0.03] border-sm-border text-sm-amber hover:bg-sm-amber/10" onClick={() => navigate("/conflicts")}>
                        <AlertTriangle className="w-3.5 h-3.5" /> View Conflicts
                    </Button>
                </aside>
            </div>
        </>
    );
}
