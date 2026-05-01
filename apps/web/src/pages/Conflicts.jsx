import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertTriangle } from "lucide-react";
import TopBar from "../components/layout/TopBar";
import StatusBadge from "../components/widgets/StatusBadge";
import { ContributorStack } from "../components/widgets/ContributorAvatar";
import api from "../lib/api";
import { CONTRIBUTORS } from "../lib/mockData";
import { relativeTime, severityColor } from "../lib/format";

const STATUS_TABS = [
    { key: "all",          label: "All"          },
    { key: "open",         label: "Open"         },
    { key: "under_review", label: "Under Review" },
    { key: "resolved",     label: "Resolved"     },
    { key: "deferred",     label: "Deferred"     },
];

export default function Conflicts() {
    const navigate = useNavigate();
    const [status, setStatus] = useState("all");
    const [conflicts, setConflicts] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        setLoading(true);
        api.listConflicts("ws", { status }).then(r => {
            setConflicts(r.conflicts);
            setLoading(false);
        });
    }, [status]);

    return (
        <>
            <TopBar
                title="Conflicts"
                subtitle={`${conflicts.length} ${status !== "all" ? status.replace("_", " ") + " " : ""}conflict${conflicts.length === 1 ? "" : "s"}`}
            />
            <div className="flex-1 px-8 py-6 space-y-5">
                <div className="flex items-center gap-1 bg-sm-surface border border-sm-border rounded-lg p-1 w-fit" data-testid="conflicts-tabs">
                    {STATUS_TABS.map((t) => (
                        <button
                            key={t.key}
                            data-testid={`conflict-tab-${t.key}`}
                            onClick={() => setStatus(t.key)}
                            className={`h-8 px-3.5 rounded-md text-[12px] font-medium transition-colors ${
                                status === t.key
                                    ? "bg-sm-blue/15 text-sm-blue"
                                    : "text-sm-text-secondary hover:text-sm-text"
                            }`}
                        >
                            {t.label}
                        </button>
                    ))}
                </div>

                {loading ? (
                    <div className="space-y-3">
                        {Array.from({ length: 3 }).map((_, i) => <div key={i} className="sm-card h-[180px] shimmer" />)}
                    </div>
                ) : conflicts.length === 0 ? (
                    <div className="sm-card py-20 text-center">
                        <AlertTriangle className="w-10 h-10 text-sm-text-muted mx-auto mb-3" strokeWidth={1.5} />
                        <p className="text-sm-text">No conflicts in this state.</p>
                        <p className="text-[12px] text-sm-text-secondary mt-1">Enjoy the peace.</p>
                    </div>
                ) : (
                    <div className="space-y-3">
                        {conflicts.map((c) => {
                            const people = c.contributors.map(l => CONTRIBUTORS.find(x => x.login === l)).filter(Boolean);
                            return (
                                <div
                                    key={c.id}
                                    data-testid={`conflict-card-${c.id}`}
                                    onClick={() => navigate(`/conflicts/${c.id}`)}
                                    className="sm-card p-5 cursor-pointer"
                                >
                                    <div className="flex items-center justify-between mb-3">
                                        <div className="flex items-center gap-2.5">
                                            <StatusBadge status={c.status} />
                                            <span className="font-mono text-[11px] text-sm-text-secondary">{c.id}</span>
                                        </div>
                                        <div className="flex items-center gap-3">
                                            <span className="font-mono text-[10.5px] uppercase tracking-wider" style={{ color: severityColor(c.severity) }}>
                                                ● {c.severity} severity
                                            </span>
                                            <span className="font-mono text-[11px] text-sm-text-secondary">{relativeTime(c.detected_at)}</span>
                                        </div>
                                    </div>

                                    <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_1fr] gap-3 items-start mb-4">
                                        <ConflictExcerpt text={c.memory_a_excerpt} label="MEMORY A" color="#4F7EFF" />
                                        <div className="flex items-center justify-center">
                                            <span className="font-mono text-[10.5px] text-sm-text-muted uppercase tracking-wider px-2 py-1 rounded bg-sm-border/30">vs</span>
                                        </div>
                                        <ConflictExcerpt text={c.memory_b_excerpt} label="MEMORY B" color="#A78BFA" />
                                    </div>

                                    <div className="flex items-center justify-between pt-3 border-t border-sm-border">
                                        <div className="flex items-center gap-3">
                                            <ContributorStack contributors={people} size={22} />
                                            <span className="font-mono text-[11px] text-sm-text-secondary">{c.conflict_type}</span>
                                        </div>
                                        <button
                                            data-testid={`start-review-${c.id}`}
                                            className="h-8 px-3 rounded-md bg-sm-blue/15 border border-sm-blue/30 text-sm-blue text-[11.5px] font-medium hover:bg-sm-blue/25"
                                            onClick={(e) => { e.stopPropagation(); navigate(`/conflicts/${c.id}`); }}
                                        >
                                            Start Review →
                                        </button>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>
        </>
    );
}

function ConflictExcerpt({ text, label, color }) {
    return (
        <div className="rounded-lg border border-sm-border bg-sm-bg/40 p-3">
            <div className="font-mono text-[10px] uppercase tracking-wider mb-1.5" style={{ color }}>{label}</div>
            <p className="text-[12.5px] text-sm-text leading-snug line-clamp-3">{text}</p>
        </div>
    );
}
