import React, { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, CheckCircle2 } from "lucide-react";
import PageHeader from "../components/ui-kit/PageHeader";
import InlineBar from "../components/ui-kit/InlineBar";
import { Skeleton } from "../components/ui-kit/Skeleton";
import ErrorState from "../components/ui-kit/ErrorState";
import useApiResource from "../hooks/useApiResource";
import StatusBadge from "../components/widgets/StatusBadge";
import { Textarea } from "../components/ui/textarea";
import { Button } from "../components/ui/button";
import { toast } from "sonner";
import api from "../lib/api";
import { relativeTime, severityColor } from "../lib/format";

/**
 * Page 5b of the Supermemory-console redesign.
 *
 * Verified against the authoritative response model (ConflictDetail in
 * apps/api/sourcemind/schemas/conflict.py) and a real populated response, not
 * against the empty corpus. Five defects, every one of which would fire the
 * moment a single conflict exists:
 *
 *   1. `c.contributors.map(...)` (old line 53) — `contributors` is not a field
 *      on ConflictDetail at all. Previously logged as "unreachable, zero
 *      conflicts exist"; unreachable is not fixed. It threw on the first
 *      conflict, and no re-pointing is possible because the API does not carry
 *      authorship here — so the contributor UI is removed rather than rewired.
 *   2. `c.memory_a_excerpt` / `c.memory_b_excerpt` — the fields are
 *      `memory_a.content` / `memory_b.content` (a nested MemoryRef).
 *   3. `c.detected_at` — the field is `created_at`.
 *   4. `c.severity.toUpperCase()` — severity is nullable
 *      (`ConflictSeverityLiteral | None`), so this threw on a null severity.
 *   5. "last edited 2d ago" was a hardcoded string rendered as fact.
 *
 * RESOLUTION VOCABULARY MISMATCH. resolve_conflict accepts exactly
 * kept_a | kept_b | merged | split | deferred. This page was sending
 * accept_a | accept_b | merge | mark_outdated | defer — none of which match,
 * so every button raised ValueError: Unknown resolution_type and returned 500.
 * The two that map cleanly are wired to their real values. The remaining three
 * need payload fields the client does not send (merged requires
 * merged_content, deferred requires revisit_at, split requires tag_a/tag_b)
 * and mark_outdated has no backend equivalent, so they are shown disabled with
 * the reason rather than left as guaranteed-500 buttons.
 */

const OPTIONS = [
    { key: "kept_a", label: "Accept A", enabled: true },
    { key: "kept_b", label: "Accept B", enabled: true },
    {
        key: "merged", label: "Merge both", enabled: false,
        reason: "Needs merged content, which this screen does not collect yet",
    },
    {
        key: "deferred", label: "Defer", enabled: false,
        reason: "Needs a revisit date, which this screen does not collect yet",
    },
    {
        key: "split", label: "Split by tag", enabled: false,
        reason: "Needs two tags, which this screen does not collect yet",
    },
];

const STAGES = ["open", "under_review", "resolved"];

export default function ConflictDetail() {
    const { id } = useParams();
    const navigate = useNavigate();
    const [selected, setSelected] = useState(null);
    const [note, setNote] = useState("");
    const [submitting, setSubmitting] = useState(false);

    // `.catch(() => setC(false))` collapsed 401, 403, 404, 429 and an offline
    // browser into one "could not be loaded" line. resetKey = id so navigating
    // between conflicts cannot show the previous one's data.
    const { data: c, error, loading, retry } = useApiResource(
        () => api.getConflict(id),
        { resetKey: id }
    );

    const submit = async () => {
        if (!selected) return;
        setSubmitting(true);
        try {
            await api.resolveConflict(id, { resolution_type: selected, note });
            toast.success("Conflict resolved", {
                description: `${OPTIONS.find(o => o.key === selected).label} · saved.`,
            });
            setTimeout(() => navigate("/conflicts"), 400);
        } catch (e) {
            // Previously unhandled: a rejected resolve left the button stuck in
            // "Saving…" and reported success was never shown nor failure.
            toast.error("Could not resolve conflict", {
                description: e?.body?.error?.message || e?.message || "The API rejected the request.",
            });
        } finally {
            setSubmitting(false);
        }
    };

    if (error) {
        return (
            <>
                <PageHeader title="Conflict" subtitle="This conflict could not be loaded." />
                <div className="sm-card">
                    <ErrorState error={error} onRetry={retry} />
                </div>
            </>
        );
    }

    if (loading || !c) {
        return (
            <>
                <PageHeader title="Conflict" subtitle="Loading…" />
                <div
                    className="grid grid-cols-1 lg:grid-cols-[1.4fr_1fr] gap-4"
                    role="status"
                    aria-busy="true"
                    aria-label="Loading conflict"
                >
                    <div className="space-y-4">
                        <div className="sm-card p-5 space-y-3">
                            <Skeleton className="h-4 w-24" />
                            <Skeleton className="h-[1em] w-full" />
                            <Skeleton className="h-[1em] w-4/5" />
                        </div>
                        <Skeleton className="h-4 w-40 mx-auto" />
                        <div className="sm-card p-5 space-y-3">
                            <Skeleton className="h-4 w-24" />
                            <Skeleton className="h-[1em] w-full" />
                            <Skeleton className="h-[1em] w-3/5" />
                        </div>
                    </div>
                    <div className="space-y-4">
                        <Skeleton className="h-[112px] w-full rounded-card" />
                        <Skeleton className="h-[280px] w-full rounded-card" />
                    </div>
                    <span className="sr-only">Loading conflict…</span>
                </div>
            </>
        );
    }

    const severity = c.severity || null;
    const currentStageIdx = STAGES.indexOf(c.status === "deferred" ? "open" : c.status);
    const sim = typeof c.similarity_score === "number"
        ? Math.round(c.similarity_score * 100)
        : null;

    const subtitleParts = [
        severity ? `${severity} severity` : null,
        c.conflict_type,
        c.created_at ? `detected ${relativeTime(c.created_at)}` : null,
    ].filter(Boolean);

    return (
        <>
            <PageHeader
                title="Conflict"
                subtitle={subtitleParts.join(" · ") || "No metadata recorded"}
                action={
                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => navigate("/conflicts")}
                        className="text-content-secondary"
                        data-testid="conflict-back"
                    >
                        <ArrowLeft className="w-4 h-4" /> Back
                    </Button>
                }
            />

            <div className="flex items-center gap-2.5 mb-5">
                <StatusBadge status={c.status} />
                {severity && (
                    <span
                        className="font-mono text-[10.5px] uppercase tracking-wider"
                        style={{ color: severityColor(severity) }}
                    >
                        ● {severity}
                    </span>
                )}
                <code className="font-mono text-[11px] text-content-secondary select-all">{c.id}</code>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-[1.4fr_1fr] gap-4">
                <section className="space-y-4">
                    <ExcerptCard label="Memory A" memory={c.memory_a} />
                    <div className="flex items-center gap-3">
                        <div className="flex-1 h-px bg-hairline" />
                        <span className="sm-micro-label px-2 py-0.5 rounded-md border border-hairline bg-surface">
                            {c.conflict_type || "conflict"}
                        </span>
                        <div className="flex-1 h-px bg-hairline" />
                    </div>
                    <ExcerptCard label="Memory B" memory={c.memory_b} />

                    {c.explanation && (
                        <div className="sm-card p-5">
                            <h2 className="sm-micro-label mb-2">Why this was flagged</h2>
                            <p className="text-body text-content leading-relaxed">{c.explanation}</p>
                            {sim !== null && (
                                <div className="flex items-center gap-2 mt-4 pt-4 border-t border-hairline">
                                    <span className="sm-micro-label">Similarity</span>
                                    <InlineBar value={sim} width="96px" />
                                </div>
                            )}
                        </div>
                    )}
                </section>

                <aside className="space-y-4">
                    <section className="sm-card p-5">
                        <h2 className="text-title text-content mb-4">Status</h2>
                        <div className="flex items-center">
                            {STAGES.map((s, i) => {
                                const active = i <= currentStageIdx;
                                return (
                                    <React.Fragment key={s}>
                                        <div className="flex flex-col items-center flex-1">
                                            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-[11px] font-semibold font-mono ${
                                                active ? "bg-brand-fill text-white" : "bg-surface-hover text-content-muted"
                                            }`}>
                                                {active ? <CheckCircle2 className="w-4 h-4" /> : i + 1}
                                            </div>
                                            <span
                                                className="mt-2 sm-micro-label"
                                                style={active ? { color: "var(--c-text-primary)" } : undefined}
                                            >
                                                {s.replace("_", " ")}
                                            </span>
                                        </div>
                                        {i < STAGES.length - 1 && (
                                            <div className={`h-0.5 flex-1 ${i < currentStageIdx ? "bg-brand" : "bg-hairline"}`} />
                                        )}
                                    </React.Fragment>
                                );
                            })}
                        </div>
                    </section>

                    <section className="sm-card p-5">
                        <h2 className="text-title text-content mb-4">Resolve</h2>
                        <div className="grid grid-cols-1 gap-2 mb-4">
                            {OPTIONS.map((o) => (
                                <button
                                    key={o.key}
                                    data-testid={`resolve-${o.key}`}
                                    onClick={() => o.enabled && setSelected(o.key)}
                                    disabled={!o.enabled}
                                    title={o.enabled ? undefined : o.reason}
                                    /* §2: selection is the accent; the old
                                       per-option colour dots were five hues
                                       used decoratively. */
                                    className={`h-10 px-4 rounded-md text-body font-medium transition-colors
                                                flex items-center justify-between sm-focusable ${
                                        !o.enabled
                                            ? "bg-surface-page border border-hairline text-content-muted cursor-not-allowed"
                                            : selected === o.key
                                                ? "bg-brand/10 border border-brand/40 text-content"
                                                : "bg-surface border border-hairline text-content-secondary hover:text-content hover:border-hairline-hover"
                                    }`}
                                >
                                    <span>{o.label}</span>
                                    {selected === o.key && o.enabled && (
                                        <CheckCircle2 className="w-4 h-4 text-brand" />
                                    )}
                                </button>
                            ))}
                        </div>

                        <label htmlFor="resolve-note" className="sm-micro-label block mb-2">
                            Resolution note
                        </label>
                        <Textarea
                            id="resolve-note"
                            data-testid="resolve-note"
                            value={note}
                            onChange={(e) => setNote(e.target.value)}
                            placeholder="Not saved yet — see note below"
                            rows={3}
                            disabled
                            className="bg-surface-page border-hairline text-content placeholder:text-content-muted text-body resize-none mb-2 disabled:opacity-60"
                        />
                        {/* Disabled rather than silently discarding input: the
                            API field is `resolution_note` and the client sends
                            `note`, which Pydantic drops. A box that accepts
                            text and throws it away is worse than one that says
                            so. */}
                        <p className="text-[11px] text-content-muted mb-4 leading-snug">
                            Notes aren't persisted yet — the client sends <code className="font-mono">note</code> where
                            the API expects <code className="font-mono">resolution_note</code>.
                        </p>

                        <Button
                            data-testid="confirm-resolution"
                            onClick={submit}
                            disabled={!selected || submitting}
                            className="w-full bg-brand-fill hover:bg-brand-fill-hover text-white disabled:opacity-40"
                        >
                            {submitting ? "Saving…" : "Confirm resolution"}
                        </Button>
                    </section>
                </aside>
            </div>
        </>
    );
}

function ExcerptCard({ label, memory }) {
    // `memory` is a MemoryRef: { id, content }. The old code read a flat
    // `memory_a_excerpt` string that the API never returned.
    return (
        <div className="sm-card p-5">
            <div className="flex items-center justify-between gap-3 mb-3">
                <span className="sm-micro-label">{label}</span>
                {memory?.id && (
                    <code className="font-mono text-[10.5px] text-content-muted truncate select-all">
                        {memory.id}
                    </code>
                )}
            </div>
            {/* §3: memory content is data → mono. */}
            <p className="font-mono text-[12.5px] text-content leading-relaxed">
                {memory?.content || "—"}
            </p>
        </div>
    );
}
