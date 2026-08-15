import React, { useEffect, useMemo, useState } from "react";
import { ArrowRight, Sparkles, Calendar, FileText, Brain } from "lucide-react";
import TopBar from "../components/layout/TopBar";
import ContributorAvatar from "../components/widgets/ContributorAvatar";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Input } from "../components/ui/input";
import { Textarea } from "../components/ui/textarea";
import { Button } from "../components/ui/button";
import { toast } from "sonner";
import api from "../lib/api";
import { relativeTime, tierColor } from "../lib/format";
import { stripMarkdown } from "../lib/format";

const STATUS_ORDER = ["initiated", "assigned", "complete"];

export default function Handoff() {
    const [members, setMembers]       = useState([]);
    const [handoffs, setHandoffs]     = useState([]);
    const [departingId, setDepartingId] = useState("");
    const [receivingId, setReceivingId] = useState("");
    const [date, setDate]             = useState("");
    const [notes, setNotes]           = useState("");
    const [classified, setClassified] = useState(null);
    const [classifying, setClassifying] = useState(false);

    useEffect(() => {
        api.listContributors().then(r => setMembers(r.contributors));
        api.listHandoffs("ws").then(r => setHandoffs(r.handoffs || [])).catch(() => setHandoffs([]));
    }, []);

    // id → contributor lookup for the active-handoff avatars
    const byId = useMemo(() => {
        const m = {};
        for (const c of members) m[c.id] = c;
        return m;
    }, [members]);

    const classify = async () => {
        if (!departingId || !receivingId || !date) {
            toast.error("Please fill all fields first.");
            return;
        }
        setClassifying(true);
        try {
            const r = await api.classifyHandoff({
                departing_id: departingId,
                receiving_id: receivingId,
                departure_date: date,
                notes,
            });
            setClassified(r);
            toast.success("Handoff classified", {
                description: `${r.tier_1_critical?.length ?? 0} critical · ${r.tier_2_important?.length ?? 0} important · ${r.tier_3_standard_count ?? 0} standard`,
            });
            // Refresh the active list so the new record shows up
            api.listHandoffs("ws").then(rr => setHandoffs(rr.handoffs || [])).catch(() => {});
        } catch (e) {
            toast.error("Classification failed", { description: e.message || "Try again" });
        } finally {
            setClassifying(false);
        }
    };

    const departing = members.find(m => m.id === departingId);

    return (
        <>
            <TopBar
                title="Handoff"
                subtitle="Transfer knowledge when team members leave — before it's too late."
            />
            <div className="flex-1 px-8 py-6 space-y-5">
                {/* Banner */}
                <div className="rounded-xl border border-sm-blue/30 bg-gradient-to-br from-sm-blue/10 via-sm-purple/5 to-transparent p-6 relative overflow-hidden">
                    <div className="absolute top-0 right-0 w-64 h-64 rounded-full bg-sm-blue/10 blur-3xl pointer-events-none -translate-y-20 translate-x-20" />
                    <div className="relative">
                        <div className="text-[11px] uppercase tracking-[0.18em] text-sm-blue font-semibold mb-2">Knowledge Transfer Center</div>
                        <h2 className="text-[20px] font-semibold text-sm-text mb-1">Prevent knowledge loss on day one, not day zero.</h2>
                        <p className="text-[13px] text-sm-text-secondary max-w-2xl">
                            SourceMind classifies a departing member's memories into three tiers and suggests the best-fit successor for every CRITICAL item. Transfer CRITICAL tier before departure.
                        </p>
                    </div>
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] gap-4">
                    {/* Initiate form */}
                    <section className="sm-card p-6 space-y-4">
                        <h3 className="text-[15px] font-semibold text-sm-text">Initiate Handoff</h3>

                        <Field label="Departing member">
                            <Select value={departingId} onValueChange={setDepartingId}>
                                <SelectTrigger data-testid="handoff-departing" className="bg-sm-bg/60 border-sm-border text-sm-text h-11">
                                    <SelectValue placeholder="Select member" />
                                </SelectTrigger>
                                <SelectContent className="bg-sm-surface border-sm-border">
                                    {members.map((m) => (
                                        <SelectItem key={m.id} value={m.id} className="text-sm-text focus:bg-white/5 focus:text-sm-text">
                                            <div className="flex items-center gap-2">
                                                <span className="w-2 h-2 rounded-full" style={{ background: m.avatarColor }} />
                                                {m.name}
                                            </div>
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </Field>

                        <Field label="Transfer to">
                            <Select value={receivingId} onValueChange={setReceivingId}>
                                <SelectTrigger data-testid="handoff-receiving" className="bg-sm-bg/60 border-sm-border text-sm-text h-11">
                                    <SelectValue placeholder="Select receiving member" />
                                </SelectTrigger>
                                <SelectContent className="bg-sm-surface border-sm-border">
                                    {members.filter(m => m.id !== departingId).map((m) => (
                                        <SelectItem key={m.id} value={m.id} className="text-sm-text focus:bg-white/5 focus:text-sm-text">
                                            <div className="flex items-center gap-2">
                                                <span className="w-2 h-2 rounded-full" style={{ background: m.avatarColor }} />
                                                {m.name}
                                            </div>
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </Field>

                        <Field label="Departure date">
                            <div className="relative">
                                <Calendar className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-sm-text-muted pointer-events-none" />
                                <Input
                                    data-testid="handoff-date"
                                    type="date"
                                    value={date}
                                    onChange={(e) => setDate(e.target.value)}
                                    className="h-11 pl-9 bg-sm-bg/60 border-sm-border text-sm-text font-mono"
                                />
                            </div>
                        </Field>

                        <Field label="Notes">
                            <Textarea
                                data-testid="handoff-notes"
                                value={notes}
                                onChange={(e) => setNotes(e.target.value)}
                                rows={3}
                                placeholder="Context about the transfer..."
                                className="bg-sm-bg/60 border-sm-border text-sm-text placeholder:text-sm-text-muted resize-none text-[12.5px]"
                            />
                        </Field>

                        <Button
                            data-testid="classify-initiate"
                            onClick={classify}
                            disabled={classifying}
                            className="w-full h-10 bg-sm-blue hover:bg-sm-blue/90 text-white"
                        >
                            <Sparkles className="w-4 h-4" /> {classifying ? "Classifying…" : "Classify & Initiate"}
                        </Button>

                        {classified && (
                            <ClassifiedResult
                                data={classified}
                                byId={byId}
                                departing={departing}
                            />
                        )}
                    </section>

                    {/* Active list */}
                    <section className="sm-card p-6">
                        <div className="flex items-center justify-between mb-5">
                            <h3 className="text-[15px] font-semibold text-sm-text">Active Handoffs</h3>
                            <span className="font-mono text-[11px] text-sm-text-secondary">{handoffs.length} in progress</span>
                        </div>

                        {handoffs.length === 0 ? (
                            <div className="text-center py-12 text-sm-text-secondary text-[12.5px]">
                                No active handoffs. Initiate one on the left to get started.
                            </div>
                        ) : (
                            <div className="space-y-3">
                                {handoffs.map((h) => (
                                    <ActiveHandoffCard key={h.id} handoff={h} byId={byId} />
                                ))}
                            </div>
                        )}
                    </section>
                </div>
            </div>
        </>
    );
}

function Field({ label, children }) {
    return (
        <div>
            <label className="block text-[12px] text-sm-text-secondary mb-2 font-medium">{label}</label>
            {children}
        </div>
    );
}

function ClassifiedResult({ data, byId, departing }) {
    const criticalCount  = data.tier_1_critical?.length ?? 0;
    const importantCount = data.tier_2_important?.length ?? 0;
    const standardCount  = data.tier_3_standard_count   ?? 0;

    return (
        <div className="rounded-lg border border-sm-blue/30 bg-sm-blue/5 p-4 space-y-4 fade-in-up">
            <div className="flex items-center justify-between">
                <span className="font-mono text-[10.5px] font-semibold px-2 py-0.5 rounded-md tracking-wider bg-sm-blue/15 border border-sm-blue/30 text-sm-blue">
                    CLASSIFICATION COMPLETE
                </span>
                <span className="font-mono text-[11px] text-sm-text-secondary">
                    {data.total_memories} memor{data.total_memories === 1 ? "y" : "ies"}
                </span>
            </div>

            {/* 3 tier counts */}
            <div className="grid grid-cols-3 gap-2">
                <TierCount label="CRITICAL"  count={criticalCount}  tier="CRITICAL"  />
                <TierCount label="IMPORTANT" count={importantCount} tier="IMPORTANT" />
                <TierCount label="STANDARD"  count={standardCount}  tier="STANDARD"  />
            </div>

            {/* Critical memories preview with suggested successors */}
            {data.tier_1_critical?.length > 0 && (
                <div>
                    <div className="font-mono text-[10.5px] text-sm-text-secondary uppercase tracking-wider mb-2">
                        Top critical memories → suggested successors
                    </div>
                    <div className="space-y-2">
                        {data.tier_1_critical.slice(0, 3).map((m) => (
                            <CriticalRow key={m.memory_id} mem={m} byId={byId} />
                        ))}
                    </div>
                </div>
            )}

            {departing && (
                <p className="text-[11.5px] text-sm-text-secondary pt-2 border-t border-sm-border">
                    Handoff <span className="font-mono text-sm-text">{data.handoff_record_id}</span> created for{" "}
                    <span className="font-mono text-sm-text">{data.departing_user_name || departing.name}</span>.
                </p>
            )}
        </div>
    );
}

function TierCount({ label, count, tier }) {
    const color = tierColor(tier);
    return (
        <div
            className="rounded-md p-3 border text-center"
            style={{ borderColor: `${color}40`, background: `${color}10` }}
        >
            <div className="font-mono text-[9.5px] uppercase tracking-wider mb-1" style={{ color }}>{label}</div>
            <div className="font-mono text-[22px] font-semibold text-sm-text leading-none">{count}</div>
        </div>
    );
}

function CriticalRow({ mem, byId }) {
    const successor = byId[mem.suggested_successor_id];
    return (
        <div className="rounded-md border border-sm-border bg-sm-bg/40 p-3">
            <div className="flex items-start gap-2.5">
                <Brain className="w-3.5 h-3.5 text-sm-blue mt-0.5 shrink-0" strokeWidth={2} />
                <div className="min-w-0 flex-1">
                    <p className="text-[12px] text-sm-text leading-snug line-clamp-2">
                        {stripMarkdown(mem.content)}
                    </p>
                    <div className="flex items-center gap-2 mt-2 text-[10.5px] font-mono text-sm-text-secondary">
                        <ArrowRight className="w-3 h-3" />
                        {successor ? (
                            <ContributorAvatar contributor={successor} size={16} />
                        ) : (
                            <span className="w-4 h-4 rounded-full bg-sm-border inline-block" />
                        )}
                        <span className="text-sm-text">{mem.suggested_successor_name || "—"}</span>
                        <span className="ml-auto">
                            importance {mem.importance_score?.toFixed(2)} · confidence {mem.successor_confidence?.toFixed(2)}
                        </span>
                    </div>
                </div>
            </div>
        </div>
    );
}

function ActiveHandoffCard({ handoff, byId }) {
    const departing = byId[handoff.departing_user_id] || {
        name: handoff.departing_user_name || handoff.departing_user_id,
        login: handoff.departing_user_id,
        avatarColor: "#4F7EFF",
    };
    // Backend returns receiving_user_name directly; may be null on freshly-initiated
    // handoffs that haven't had their first /assign call yet.
    const hasReceiver = !!handoff.receiving_user_name;
    const receivingContrib = handoff.receiving_user_id ? byId[handoff.receiving_user_id] : null;
    const receiving = hasReceiver
        ? {
            name: handoff.receiving_user_name,
            login: receivingContrib?.login || handoff.receiving_user_id,
            avatarColor: receivingContrib?.avatarColor || "#A78BFA",
        }
        : null;
    const stageIdx = STATUS_ORDER.indexOf(handoff.status);
    const color = tierColor(handoff.tier);

    return (
        <div data-testid={`handoff-${handoff.id}`} className="rounded-lg border border-sm-border bg-sm-bg/40 p-4">
            <div className="flex items-center gap-3 mb-4">
                <ContributorAvatar contributor={departing} size={32} />
                <ArrowRight className="w-4 h-4 text-sm-text-secondary shrink-0" />
                {receiving ? (
                    <ContributorAvatar contributor={receiving} size={32} />
                ) : (
                    <div
                        className="w-8 h-8 rounded-full border border-dashed border-sm-border bg-sm-bg flex items-center justify-center text-sm-text-muted text-[10px] font-mono shrink-0"
                        title="Not yet assigned"
                    >
                        ?
                    </div>
                )}
                <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-medium text-sm-text truncate">
                        {departing.name} → {receiving ? receiving.name : <span className="text-sm-text-muted italic">unassigned</span>}
                    </div>
                    <div className="font-mono text-[10.5px] text-sm-text-secondary truncate">
                        initiated {relativeTime(handoff.created_at)}
                    </div>
                </div>
                <span
                    className="font-mono text-[10.5px] font-semibold px-2 py-0.5 rounded-md tracking-wider shrink-0"
                    style={{ background: `${color}20`, color, border: `1px solid ${color}40` }}
                >
                    {handoff.tier}
                </span>
            </div>

            <div className="grid grid-cols-3 gap-4 mb-4">
                <Stat label="memories"    value={handoff.total_memories} icon={FileText} />
                <Stat label="status"      value={handoff.status.replace("_", " ")} />
                <Stat label="record"      value={<span className="font-mono text-[11px]">{handoff.id}</span>} />
            </div>

            <div className="flex items-center gap-2 mb-3">
                {STATUS_ORDER.map((s, i) => (
                    <div
                        key={s}
                        className="flex-1 h-1.5 rounded-full transition-colors"
                        style={{ background: i <= stageIdx ? "#4F7EFF" : "#1E1E2E" }}
                    />
                ))}
            </div>
            <div className="flex items-center justify-between">
                <span className="font-mono text-[10.5px] text-sm-text-secondary uppercase tracking-wider">
                    {handoff.status}
                </span>
                <div className="flex gap-2">
                    {handoff.status !== "complete" && (
                        <>
                            <button className="h-7 px-3 rounded-md bg-white/[0.04] border border-sm-border text-[11.5px] text-sm-text hover:bg-white/[0.08]">Assign</button>
                            <button className="h-7 px-3 rounded-md bg-sm-green/15 border border-sm-green/30 text-[11.5px] text-sm-green hover:bg-sm-green/25">Complete</button>
                        </>
                    )}
                </div>
            </div>
        </div>
    );
}

function Stat({ label, value, icon: Icon }) {
    return (
        <div>
            <div className="font-mono text-[10px] text-sm-text-secondary uppercase mb-0.5 flex items-center gap-1">
                {Icon && <Icon className="w-2.5 h-2.5" />}
                {label}
            </div>
            <div className="font-mono text-[14px] text-sm-text">{value}</div>
        </div>
    );
}
