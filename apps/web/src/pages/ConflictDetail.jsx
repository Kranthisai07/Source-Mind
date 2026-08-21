import React, { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, CheckCircle2 } from "lucide-react";
import TopBar from "../components/layout/TopBar";
import StatusBadge from "../components/widgets/StatusBadge";
import ContributorAvatar from "../components/widgets/ContributorAvatar";
import { Textarea } from "../components/ui/textarea";
import { Button } from "../components/ui/button";
import { toast } from "sonner";
import api from "../lib/api";
import { CONTRIBUTORS } from "../lib/mockData";
import { relativeTime, severityColor } from "../lib/format";

const OPTIONS = [
    { key: "accept_a",     label: "Accept A",      color: "#34D399" },
    { key: "accept_b",     label: "Accept B",      color: "#4F7EFF" },
    { key: "merge",        label: "Merge Both",    color: "#A78BFA" },
    { key: "mark_outdated", label: "Mark Outdated", color: "#F59E0B" },
    { key: "defer",        label: "Defer",         color: "#8888A8" },
];

const STAGES = ["open", "under_review", "resolved"];

export default function ConflictDetail() {
    const { id } = useParams();
    const navigate = useNavigate();
    const [c, setC] = useState(null);
    const [selected, setSelected] = useState(null);
    const [note, setNote] = useState("");
    const [submitting, setSubmitting] = useState(false);

    useEffect(() => { api.getConflict(id).then(setC); }, [id]);

    const submit = async () => {
        if (!selected) return;
        setSubmitting(true);
        await api.resolveConflict(id, { resolution_type: selected, note });
        toast.success("Conflict resolved", { description: `${OPTIONS.find(o => o.key === selected).label} · saved.` });
        setSubmitting(false);
        setTimeout(() => navigate("/conflicts"), 400);
    };

    if (!c) {
        return <>
            <TopBar title="Conflict" subtitle="loading…" />
            <div className="flex-1 px-8 py-6 grid grid-cols-2 gap-4">
                <div className="sm-card h-[500px] shimmer" />
                <div className="sm-card h-[500px] shimmer" />
            </div>
        </>;
    }

    const people = c.contributors.map(l => CONTRIBUTORS.find(x => x.login === l));
    const currentStageIdx = STAGES.indexOf(c.status === "deferred" ? "open" : c.status);

    return (
        <>
            <TopBar
                title={<span className="font-mono text-[14px]">{c.id}</span>}
                subtitle={<>
                    <StatusBadge status={c.status} /> · <span style={{ color: severityColor(c.severity) }}>{c.severity.toUpperCase()}</span> · detected {relativeTime(c.detected_at)}
                </>}
                actions={
                    <Button variant="ghost" size="sm" onClick={() => navigate("/conflicts")} className="text-sm-text-secondary" data-testid="conflict-back">
                        <ArrowLeft className="w-4 h-4" /> Back
                    </Button>
                }
            />
            <div className="flex-1 px-8 py-6 grid grid-cols-1 lg:grid-cols-[1.4fr_1fr] gap-4">
                {/* Left — memories + AI */}
                <section className="space-y-4">
                    <ExcerptCard
                        label="MEMORY A"
                        text={c.memory_a_excerpt}
                        contributor={people[0]}
                        color="#4F7EFF"
                    />
                    <div className="flex items-center gap-3">
                        <div className="flex-1 h-px bg-sm-border" />
                        <span className="font-mono text-[11px] uppercase tracking-wider text-sm-text-muted px-2 py-0.5 rounded-md border border-sm-border bg-sm-surface">
                            {c.conflict_type}
                        </span>
                        <div className="flex-1 h-px bg-sm-border" />
                    </div>
                    <ExcerptCard
                        label="MEMORY B"
                        text={c.memory_b_excerpt}
                        contributor={people[1]}
                        color="#A78BFA"
                    />

                </section>

                {/* Right — resolution */}
                <aside className="space-y-4">
                    <section className="sm-card p-5">
                        <h3 className="text-[13px] font-semibold text-sm-text mb-4">Status Timeline</h3>
                        <div className="flex items-center">
                            {STAGES.map((s, i) => {
                                const active = i <= currentStageIdx;
                                return (
                                    <React.Fragment key={s}>
                                        <div className="flex flex-col items-center flex-1">
                                            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-[11px] font-semibold font-mono ${active ? "bg-sm-blue text-white" : "bg-sm-border/50 text-sm-text-muted"}`}>
                                                {active ? <CheckCircle2 className="w-4 h-4" /> : i + 1}
                                            </div>
                                            <span className={`mt-2 text-[10.5px] font-mono uppercase tracking-wider ${active ? "text-sm-text" : "text-sm-text-muted"}`}>
                                                {s.replace("_", " ")}
                                            </span>
                                        </div>
                                        {i < STAGES.length - 1 && (
                                            <div className={`h-0.5 flex-1 ${i < currentStageIdx ? "bg-sm-blue" : "bg-sm-border"}`} />
                                        )}
                                    </React.Fragment>
                                );
                            })}
                        </div>
                    </section>

                    <section className="sm-card p-5">
                        <h3 className="text-[13px] font-semibold text-sm-text mb-4">Resolve</h3>
                        <div className="grid grid-cols-1 gap-2 mb-4">
                            {OPTIONS.map((o) => (
                                <button
                                    key={o.key}
                                    data-testid={`resolve-${o.key}`}
                                    onClick={() => setSelected(o.key)}
                                    className={`h-10 px-4 rounded-lg text-[12.5px] font-medium transition-all active:scale-[0.97] flex items-center justify-between ${
                                        selected === o.key
                                            ? "border text-sm-text"
                                            : "bg-white/[0.03] border border-sm-border text-sm-text-secondary hover:text-sm-text"
                                    }`}
                                    style={selected === o.key ? { borderColor: o.color, background: `${o.color}18` } : {}}
                                >
                                    <span className="flex items-center gap-2.5">
                                        <span className="w-2 h-2 rounded-full" style={{ background: o.color }} />
                                        {o.label}
                                    </span>
                                    {selected === o.key && <CheckCircle2 className="w-4 h-4" style={{ color: o.color }} />}
                                </button>
                            ))}
                        </div>
                        <Textarea
                            data-testid="resolve-note"
                            value={note}
                            onChange={(e) => setNote(e.target.value)}
                            placeholder="Add resolution note..."
                            rows={3}
                            className="bg-sm-bg/60 border-sm-border text-sm-text placeholder:text-sm-text-muted text-[12.5px] resize-none mb-4"
                        />
                        <Button
                            data-testid="confirm-resolution"
                            onClick={submit}
                            disabled={!selected || submitting}
                            className="w-full bg-sm-blue hover:bg-sm-blue/90 text-white disabled:opacity-40"
                        >
                            {submitting ? "Saving…" : "Confirm Resolution"}
                        </Button>
                    </section>
                </aside>
            </div>
        </>
    );
}

function ExcerptCard({ label, text, contributor, color }) {
    return (
        <div className="sm-card p-5">
            <div className="flex items-center justify-between mb-3">
                <span className="font-mono text-[10.5px] uppercase tracking-wider" style={{ color }}>{label}</span>
                {contributor && <ContributorAvatar contributor={contributor} size={24} />}
            </div>
            <p className="text-[14px] text-sm-text leading-relaxed mb-3">{text}</p>
            {contributor && (
                <div className="pt-3 border-t border-sm-border font-mono text-[11px] text-sm-text-secondary">
                    @{contributor.login} · last edited 2d ago
                </div>
            )}
        </div>
    );
}
