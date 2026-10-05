import React, { useMemo, useState } from "react";
import { ArrowRight, Sparkles, FileText, Brain } from "lucide-react";
import TopBar from "../components/layout/TopBar";
import ContributorAvatar from "../components/widgets/ContributorAvatar";
import { PageLoadError, PageLoading } from "../components/widgets/PageLoadState";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Button } from "../components/ui/button";
import { toast } from "sonner";
import useApiResource from "../hooks/useApiResource";
import api from "../lib/api";
import { relativeTime, tierColor } from "../lib/format";
import { stripMarkdown } from "../lib/format";

const STATUS_ORDER = ["in_progress", "completed"];
const EMPTY_LIST = [];

export default function Handoff() {
    const [departingId, setDepartingId] = useState("");
    const [classified, setClassified] = useState(null);
    const [classifying, setClassifying] = useState(false);

    const membersResource = useApiResource(() => api.listContributors());
    const handoffsResource = useApiResource(() => api.listHandoffs());
    const members = membersResource.data?.contributors ?? EMPTY_LIST;
    const handoffs = handoffsResource.data?.handoffs ?? EMPTY_LIST;

    // id → contributor lookup for the active-handoff avatars
    const byId = useMemo(() => {
        const m = {};
        for (const c of members) m[c.id] = c;
        return m;
    }, [members]);

    const classify = async () => {
        if (!departingId) {
            toast.error("Select a departing member first.");
            return;
        }
        setClassifying(true);
        try {
            const r = await api.classifyHandoff({ departing_id: departingId });
            setClassified(r);
            toast.success("Handoff classified", {
                description: `${r.tier_1_critical?.length ?? 0} critical · ${r.tier_2_important?.length ?? 0} important · ${r.tier_3_standard_count ?? 0} standard`,
            });
            handoffsResource.retry();
        } catch (e) {
            toast.error("Classification failed", { description: e?.body?.error?.message || e?.message || "Try again" });
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
                            SourceMind classifies a departing member's memories into three tiers and suggests a best-fit successor for a CRITICAL item when an eligible related contributor is available. Transfer CRITICAL tier before departure.
                        </p>
                    </div>
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] gap-4">
                    {/* Initiate form */}
                    <section className="sm-card p-6 space-y-4">
                        <h3 className="text-[15px] font-semibold text-sm-text">Initiate Handoff</h3>
                        <p className="text-[12.5px] text-sm-text-secondary">
                            Successors are assigned per memory after classification.
                        </p>

                        {membersResource.error ? (
                            <PageLoadError error={membersResource.error} onRetry={membersResource.retry} testId="handoff-members-error" compact />
                        ) : <Field label="Departing member">
                            <Select value={departingId} onValueChange={setDepartingId}>
                                <SelectTrigger data-testid="handoff-departing" className="bg-sm-bg/60 border-sm-border text-sm-text h-11">
                                    <SelectValue placeholder={membersResource.loading ? "Loading…" : "Select member"} />
                                </SelectTrigger>
                                <SelectContent className="bg-sm-surface border-sm-border">
                                    {members.map((m) => (
                                        <SelectItem key={m.id} value={m.id} className="text-sm-text focus:bg-white/5 focus:text-sm-text">
                                            <div className="flex items-center gap-2">
                                                <span className="w-2 h-2 rounded-full" style={{ background: m.avatarColor }} />
                                                {m.name || m.login}
                                            </div>
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </Field>}

                        <Button
                            data-testid="classify-initiate"
                            onClick={classify}
                            disabled={classifying || !departingId}
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
                            <span className="font-mono text-[11px] text-sm-text-secondary">{handoffs.length} record{handoffs.length === 1 ? "" : "s"}</span>
                        </div>

                        {handoffsResource.error ? (
                            <PageLoadError error={handoffsResource.error} onRetry={handoffsResource.retry} testId="handoffs-error" compact />
                        ) : handoffsResource.loading ? (
                            <PageLoading label="Loading handoffs" testId="handoffs-loading" />
                        ) : handoffs.length === 0 ? (
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

export function CriticalRow({ mem, byId }) {
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
                            importance {mem.importance_score?.toFixed(2)} · confidence {mem.successor_confidence == null ? "unavailable" : mem.successor_confidence.toFixed(2)}
                        </span>
                    </div>
                </div>
            </div>
        </div>
    );
}

function ActiveHandoffCard({ handoff, byId }) {
    const departing = byId[handoff.departing_user_id] || {
        name: handoff.departing_user_name || "Unknown member",
        avatarColor: "#4F7EFF",
    };
    // Backend returns receiving_user_name directly; may be null on freshly-initiated
    // handoffs that haven't had their first /assign call yet.
    const hasReceiver = !!handoff.receiving_user_name;
    const receivingContrib = handoff.receiving_user_id ? byId[handoff.receiving_user_id] : null;
    const receiving = hasReceiver
        ? {
            name: handoff.receiving_user_name,
            login: receivingContrib?.login,
            avatarColor: receivingContrib?.avatarColor || "#A78BFA",
        }
        : null;
    const tier1Count = handoff.tier_1_count ?? 0;
    const tier2Count = handoff.tier_2_count ?? 0;
    const tier3Count = handoff.tier_3_count ?? 0;
    const totalMemories = tier1Count + tier2Count + tier3Count;
    const assignedCount = handoff.assigned_count ?? 0;
    const stageIdx = STATUS_ORDER.indexOf(handoff.status);
    const color = tierColor(tier1Count > 0 ? "CRITICAL" : tier2Count > 0 ? "IMPORTANT" : "STANDARD");
    const isComplete = handoff.status === "completed";

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
                    T1 {tier1Count} · T2 {tier2Count} · T3 {tier3Count}
                </span>
            </div>

            <div className="grid grid-cols-3 gap-4 mb-4">
                <Stat label="memories" value={totalMemories} icon={FileText} />
                <Stat label="assigned" value={`${assignedCount} / ${totalMemories}`} />
                <Stat label="status" value={handoff.status.replace("_", " ")} />
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
                    {!isComplete && (
                        <>
                            <button disabled title="Per-memory assignment isn't wired up on this screen yet" className="h-7 px-3 rounded-md border border-sm-border text-[11.5px] text-sm-text-muted cursor-not-allowed">Assign</button>
                            <button disabled title="Completing a handoff isn't wired up on this screen yet" className="h-7 px-3 rounded-md border border-sm-border text-[11.5px] text-sm-text-muted cursor-not-allowed">Complete</button>
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
