import React, { useMemo, useState } from "react";
import { ArrowRight, ArrowRightLeft } from "lucide-react";
import PageHeader from "../components/ui-kit/PageHeader";
import EmptyState from "../components/ui-kit/EmptyState";
import InlineBar from "../components/ui-kit/InlineBar";
import { Skeleton } from "../components/ui-kit/Skeleton";
import ErrorState from "../components/ui-kit/ErrorState";
import useApiResource from "../hooks/useApiResource";
import StatusBadge from "../components/widgets/StatusBadge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Button } from "../components/ui/button";
import { toast } from "sonner";
import api from "../lib/api";
import { relativeTime, initials, stripMarkdown } from "../lib/format";

/**
 * Page 6 of the Supermemory-console redesign.
 *
 * Verified against a REAL populated handoff record (inserted into a disposable
 * workspace and read back through GET /v1/workspaces/{ws}/handoffs), not
 * against the empty list. WORKING_STANDARDS rule 7. Four defects:
 *
 *   1. `handoff.tier` does not exist. tierColor(undefined) returned the grey
 *      default and the badge rendered empty. The API carries tier_1_count /
 *      tier_2_count / tier_3_count instead — a distribution, not one tier.
 *   2. `handoff.total_memories` does not exist; the "memories" stat was blank.
 *      Derived here from the three tier counts (4 + 11 + 37 = 52 on the probe).
 *   3. STATUS_ORDER was ["initiated","assigned","complete"]. The DB column
 *      defaults to 'in_progress' and completes as 'completed', so indexOf()
 *      returned -1 and the progress bar showed 0/3 filled for every record,
 *      forever.
 *   4. Actions were hidden on `status !== "complete"`, but the terminal status
 *      is 'completed', so they stayed visible on finished handoffs.
 *
 * FORM CONTRACT. realApi.classifyHandoff sends only {departing_user_id};
 * InitiateHandoffBody accepts only that. The form previously collected — and
 * REQUIRED — "Transfer to", "Departure date" and "Notes", then dropped all
 * three. Users could not initiate without filling fields that were discarded.
 * They are removed rather than left as required-and-ignored: the successor is
 * chosen per memory at assign time, which is what the API models.
 */

// The two statuses handoff_records actually uses.
const STATUS_ORDER = ["in_progress", "completed"];

export default function Handoff() {
    const [departingId, setDepartingId] = useState("");
    const [classified, setClassified] = useState(null);
    const [classifying, setClassifying] = useState(false);

    const membersRes = useApiResource(() => api.listContributors());
    const handoffsRes = useApiResource(() => api.listHandoffs());

    // Memoised because `byId` below depends on it; a fresh array identity each
    // render would rebuild that map on every render.
    const members = useMemo(
        () => (membersRes.error ? [] : (membersRes.data?.contributors ?? null)),
        [membersRes.error, membersRes.data]
    );
    const handoffs = handoffsRes.error ? null : (handoffsRes.data?.handoffs ?? null);
    const loadHandoffs = handoffsRes.retry;

    const byId = useMemo(() => {
        const m = {};
        for (const c of members ?? []) m[c.id] = c;
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
            loadHandoffs();
        } catch (e) {
            toast.error("Classification failed", {
                description: e?.body?.error?.message || e?.message || "Try again",
            });
        } finally {
            setClassifying(false);
        }
    };

    const rows = handoffs ?? [];

    return (
        <>
            <PageHeader
                title="Handoff"
                subtitle="Classify a departing member's knowledge into tiers, and transfer the critical items before they go."
            />

            <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] gap-4">
                <section className="sm-card p-6 space-y-4">
                    <div>
                        <h2 className="text-title text-content">Initiate handoff</h2>
                        <p className="text-body text-content-secondary mt-0.5">
                            Successors are chosen per memory after classification, not up front.
                        </p>
                    </div>

                    <div>
                        <label className="sm-micro-label block mb-2">Departing member</label>
                        <Select value={departingId} onValueChange={setDepartingId}>
                            <SelectTrigger
                                data-testid="handoff-departing"
                                className="bg-surface-page border-hairline text-content h-10"
                            >
                                <SelectValue placeholder={members === null ? "Loading…" : "Select member"} />
                            </SelectTrigger>
                            <SelectContent className="bg-surface border-hairline">
                                {(members ?? []).map((m) => (
                                    <SelectItem key={m.id} value={m.id} className="text-content">
                                        {m.name || m.login}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>

                    <Button
                        data-testid="classify-initiate"
                        onClick={classify}
                        disabled={classifying || !departingId}
                        className="w-full h-10 bg-brand-fill hover:bg-brand-fill-hover text-white disabled:opacity-40"
                    >
                        {classifying ? "Classifying…" : "Classify & initiate"}
                    </Button>

                    {classified && <ClassifiedResult data={classified} />}
                </section>

                <section className="sm-card p-6">
                    <div className="flex items-center justify-between mb-5">
                        <h2 className="text-title text-content">Active handoffs</h2>
                        {handoffs !== null && (
                            <span className="sm-micro-label">
                                {rows.length} record{rows.length === 1 ? "" : "s"}
                            </span>
                        )}
                    </div>

                    {handoffsRes.error ? (
                        <ErrorState error={handoffsRes.error} onRetry={handoffsRes.retry} testId="handoffs-error" />
                    ) : handoffs === null ? (
                        <div className="space-y-3" role="status" aria-busy="true" aria-label="Loading handoffs">
                            {Array.from({ length: 2 }).map((_, i) => (
                                <Skeleton key={i} className="h-[168px] w-full rounded-md" />
                            ))}
                            <span className="sr-only">Loading handoffs…</span>
                        </div>
                    ) : rows.length === 0 ? (
                        <EmptyState
                            testId="handoffs-empty"
                            icon={ArrowRightLeft}
                            noun="handoffs"
                            description="When someone is leaving, classify their memories here and the record will appear in this list until every critical item is reassigned."
                        />
                    ) : (
                        <div className="space-y-3">
                            {rows.map((h) => (
                                <ActiveHandoffCard key={h.id} handoff={h} byId={byId} />
                            ))}
                        </div>
                    )}
                </section>
            </div>
        </>
    );
}

/* §2: tiers are semantic, so they use the established status tokens —
   critical is danger, important is warning, standard is neutral. The old
   tierColor() gave STANDARD the accent blue, which competed with the one
   accent the reference permits. */
// Colours are applied as inline style, not as an interpolated Tailwind class.
// `!${t.cls}` never appears as a literal string in the source, so Tailwind's
// JIT scanner cannot see it and emits no rule - the labels silently fell back
// to sm-micro-label's grey, losing the semantic colour entirely. Verified
// absent from the built CSS bundle before switching.
const TIERS = [
    { key: "critical",  label: "Critical",  color: "var(--c-status-danger)"  },
    { key: "important", label: "Important", color: "var(--c-status-warning)" },
    { key: "standard",  label: "Standard",  color: "var(--c-text-secondary)" },
];

function ClassifiedResult({ data }) {
    const counts = {
        critical:  data.tier_1_critical?.length ?? 0,
        important: data.tier_2_important?.length ?? 0,
        standard:  data.tier_3_standard_count ?? 0,
    };
    const total = counts.critical + counts.important + counts.standard;

    return (
        <div className="rounded-md border border-hairline bg-surface-page p-4 space-y-4">
            <div className="flex items-baseline justify-between gap-3">
                <span className="sm-micro-label">Classification complete</span>
                <div className="flex items-baseline gap-1.5">
                    {/* §3 hero metric */}
                    <span className="font-mono text-hero tabular-nums text-content">{total}</span>
                    <span className="text-body text-content-secondary">
                        memor{total === 1 ? "y" : "ies"}
                    </span>
                </div>
            </div>

            <div className="space-y-2.5">
                {TIERS.map((t) => (
                    <div key={t.key} className="flex items-center gap-3">
                        <span
                            className="sm-micro-label w-20 shrink-0"
                            style={{ color: t.color }}
                        >
                            {t.label}
                        </span>
                        {/* §4.5 — number and bar in one cell. */}
                        <InlineBar
                            value={counts[t.key]}
                            max={total || 1}
                            display={counts[t.key]}
                            suffix=""
                            width="100%"
                            className="flex-1"
                            barColor={t.color}
                        />
                    </div>
                ))}
            </div>

            {data.tier_1_critical?.length > 0 && (
                <div className="pt-3 border-t border-hairline">
                    <div className="sm-micro-label mb-2">Critical memories → suggested successor</div>
                    <div className="space-y-2">
                        {data.tier_1_critical.slice(0, 3).map((m) => (
                            <div key={m.memory_id} className="rounded-md border border-hairline bg-surface p-3">
                                {/* §3: memory content is data → mono. */}
                                <p className="font-mono text-[11.5px] text-content leading-snug line-clamp-2">
                                    {stripMarkdown(m.content || "")}
                                </p>
                                <div className="flex items-center gap-2 mt-2 font-mono text-[10.5px] text-content-secondary">
                                    <ArrowRight className="w-3 h-3" aria-hidden="true" />
                                    <span className="text-content">{m.suggested_successor_name || "no successor suggested"}</span>
                                    <span className="ml-auto">
                                        importance {m.importance_score?.toFixed(2) ?? "—"} · confidence {m.successor_confidence?.toFixed(2) ?? "—"}
                                    </span>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {data.handoff_record_id && (
                <p className="text-[11px] text-content-secondary pt-2 border-t border-hairline">
                    Record <code className="font-mono text-content select-all">{data.handoff_record_id}</code>
                    {data.departing_user_name ? ` created for ${data.departing_user_name}.` : "."}
                </p>
            )}
        </div>
    );
}

function ActiveHandoffCard({ handoff, byId }) {
    const departingName =
        handoff.departing_user_name
        || byId[handoff.departing_user_id]?.name
        || handoff.departing_user_id;
    const receivingName = handoff.receiving_user_name || null;

    // `tier` and `total_memories` are not fields on this response. The tier
    // DISTRIBUTION is, so it is shown as such and the total derived from it.
    const t1 = handoff.tier_1_count ?? 0;
    const t2 = handoff.tier_2_count ?? 0;
    const t3 = handoff.tier_3_count ?? 0;
    const total = t1 + t2 + t3;
    const assigned = handoff.assigned_count ?? 0;

    const stageIdx = STATUS_ORDER.indexOf(handoff.status);
    const isDone = handoff.status === "completed";

    return (
        <div
            data-testid={`handoff-${handoff.id}`}
            className="rounded-md border border-hairline bg-surface-page p-4"
        >
            <div className="flex items-center gap-3 mb-4">
                <Avatar name={departingName} />
                <ArrowRight className="w-4 h-4 text-content-muted shrink-0" aria-hidden="true" />
                {receivingName ? (
                    <Avatar name={receivingName} />
                ) : (
                    <div
                        className="w-7 h-7 rounded-full border border-dashed border-hairline
                                   flex items-center justify-center text-content-muted text-[10px] font-mono shrink-0"
                        title="Not yet assigned"
                    >
                        ?
                    </div>
                )}
                <div className="min-w-0 flex-1">
                    <div className="text-body font-medium text-content truncate">
                        {departingName} → {receivingName || (
                            <span className="text-content-muted">unassigned</span>
                        )}
                    </div>
                    <div className="font-mono text-[10.5px] text-content-secondary truncate">
                        initiated {handoff.created_at ? relativeTime(handoff.created_at) : "—"}
                    </div>
                </div>
                <StatusBadge status={handoff.status} />
            </div>

            <div className="grid grid-cols-3 gap-4 mb-4">
                <Stat label="Memories" value={total} />
                <Stat label="Assigned" value={`${assigned} / ${total}`} />
                <Stat label="Critical" value={t1} tone={t1 > 0 ? "text-danger" : undefined} />
            </div>

            {/* Assignment progress: real, derived from assigned_count. */}
            <div className="mb-3">
                <div
                    className="h-1.5 rounded-full overflow-hidden"
                    style={{ background: "var(--c-data-track)" }}
                    aria-hidden="true"
                >
                    <div
                        className="h-full rounded-full"
                        style={{
                            width: `${total > 0 ? (assigned / total) * 100 : 0}%`,
                            background: "var(--c-accent)",
                        }}
                    />
                </div>
            </div>

            <div className="flex items-center justify-between gap-3">
                <span className="sm-micro-label">
                    {stageIdx >= 0 ? handoff.status.replace("_", " ") : handoff.status}
                </span>
                {!isDone && (
                    <div className="flex gap-2">
                        {/* Disabled, not removed: assignHandoff / completeHandoff
                            exist in realApi but were never wired to these
                            buttons — they had no onClick at all and silently did
                            nothing. Wiring them is a data-layer change. */}
                        <button
                            disabled
                            title="Per-memory assignment isn't wired up on this screen yet"
                            className="h-7 px-3 rounded-md border border-hairline text-[11.5px]
                                       text-content-muted cursor-not-allowed"
                        >
                            Assign
                        </button>
                        <button
                            disabled
                            title="Completing a handoff isn't wired up on this screen yet"
                            className="h-7 px-3 rounded-md border border-hairline text-[11.5px]
                                       text-content-muted cursor-not-allowed"
                        >
                            Complete
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}

/* §2: neutral initials rather than a per-person hue. */
function Avatar({ name }) {
    return (
        <div className="w-7 h-7 rounded-full shrink-0 flex items-center justify-center
                        bg-surface-hover border border-hairline
                        text-[10px] font-semibold text-content-secondary">
            {initials(name || "?")}
        </div>
    );
}

function Stat({ label, value, tone }) {
    return (
        <div>
            <div className="sm-micro-label mb-0.5">{label}</div>
            <div className={`font-mono text-body tabular-nums ${tone || "text-content"}`}>
                {value}
            </div>
        </div>
    );
}
