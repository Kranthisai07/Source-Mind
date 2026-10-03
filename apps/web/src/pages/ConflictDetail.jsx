import React, { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, CheckCircle2 } from "lucide-react";
import TopBar from "../components/layout/TopBar";
import StatusBadge from "../components/widgets/StatusBadge";
import { PageLoadError } from "../components/widgets/PageLoadState";
import { Input } from "../components/ui/input";
import { Textarea } from "../components/ui/textarea";
import { Button } from "../components/ui/button";
import { toast } from "sonner";
import useApiResource from "../hooks/useApiResource";
import api from "../lib/api";
import { classifyApiError } from "../lib/apiError";
import { buildResolutionPayload, validateResolution } from "../lib/conflictResolution";
import { relativeTime, severityColor } from "../lib/format";

const OPTIONS = [
    { key: "kept_a", label: "Accept A", hint: "Keeps memory A; retires memory B.", color: "#34D399" },
    { key: "kept_b", label: "Accept B", hint: "Keeps memory B; retires memory A.", color: "#4F7EFF" },
    { key: "merged", label: "Merge both", hint: "Replaces both with one combined memory.", color: "#A78BFA" },
    { key: "split", label: "Split by tag", hint: "Keeps both and adds one tag to each.", color: "#F59E0B" },
    { key: "deferred", label: "Defer", hint: "Revisit later; the conflict stays open.", color: "#8888A8" },
];

const STAGES = ["open", "under_review", "resolved"];

export default function ConflictDetail() {
    const { id } = useParams();
    const navigate = useNavigate();
    const [selected, setSelected] = useState(null);
    const [note, setNote] = useState("");
    const [mergedContent, setMergedContent] = useState("");
    const [tagA, setTagA] = useState("");
    const [tagB, setTagB] = useState("");
    const [revisitAt, setRevisitAt] = useState("");
    const [fieldErrors, setFieldErrors] = useState({});
    const [submitting, setSubmitting] = useState(false);

    const resource = useApiResource(() => api.getConflict(id), { resetKey: id });
    const conflict = resource.data;
    const fields = { note, mergedContent, tagA, tagB, revisitAt };

    const submit = async () => {
        if (!selected || submitting) return;

        const validation = validateResolution(selected, fields);
        setFieldErrors(validation.errors);
        if (!validation.ok) return;

        setSubmitting(true);
        try {
            await api.resolveConflict(id, buildResolutionPayload(selected, fields));
            toast.success("Conflict updated", {
                description: `${OPTIONS.find((option) => option.key === selected).label} · saved.`,
            });
            await resource.retry();
            setTimeout(() => navigate("/conflicts"), 600);
        } catch (requestError) {
            const classified = classifyApiError(requestError);
            toast.error(classified.title, {
                description: requestError?.body?.error?.message || classified.detail,
            });
        } finally {
            setSubmitting(false);
        }
    };

    if (resource.error) {
        return (
            <>
                <TopBar title="Conflict" subtitle="This conflict could not be loaded" />
                <div className="flex-1 px-8 py-6">
                    <PageLoadError error={resource.error} onRetry={resource.retry} testId="conflict-detail-error" />
                </div>
            </>
        );
    }

    if (resource.loading || !conflict) {
        return (
            <>
                <TopBar title="Conflict" subtitle="Loading…" />
                <div className="flex-1 px-8 py-6 grid grid-cols-2 gap-4" role="status" aria-label="Loading conflict">
                    <div className="sm-card h-[500px] shimmer" />
                    <div className="sm-card h-[500px] shimmer" />
                </div>
            </>
        );
    }

    const severity = conflict.severity || null;
    const currentStageIdx = STAGES.indexOf(conflict.status === "deferred" ? "open" : conflict.status);
    const similarity = typeof conflict.similarity_score === "number"
        ? Math.round(conflict.similarity_score * 100)
        : null;

    return (
        <>
            <TopBar
                title={<span className="font-mono text-[14px]">{conflict.id}</span>}
                subtitle={(
                    <span className="inline-flex flex-wrap items-center gap-1.5">
                        <StatusBadge status={conflict.status} />
                        {severity && (
                            <>
                                <span>·</span>
                                <span style={{ color: severityColor(severity) }}>{severity.toUpperCase()}</span>
                            </>
                        )}
                        {conflict.conflict_type && <><span>·</span><span>{conflict.conflict_type}</span></>}
                        {conflict.created_at && <><span>·</span><span>detected {relativeTime(conflict.created_at)}</span></>}
                    </span>
                )}
                actions={(
                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => navigate("/conflicts")}
                        className="text-sm-text-secondary"
                        data-testid="conflict-back"
                    >
                        <ArrowLeft className="w-4 h-4" /> Back
                    </Button>
                )}
            />

            <div className="flex-1 px-8 py-6 grid grid-cols-1 lg:grid-cols-[1.4fr_1fr] gap-4">
                <section className="space-y-4">
                    <ExcerptCard label="Memory A" memory={conflict.memory_a} color="#4F7EFF" />
                    <div className="flex items-center gap-3">
                        <div className="flex-1 h-px bg-sm-border" />
                        <span className="font-mono text-[10.5px] uppercase tracking-wider text-sm-text-muted px-2 py-0.5 rounded-md border border-sm-border bg-sm-surface">
                            {conflict.conflict_type || "conflict"}
                        </span>
                        <div className="flex-1 h-px bg-sm-border" />
                    </div>
                    <ExcerptCard label="Memory B" memory={conflict.memory_b} color="#A78BFA" />

                    {conflict.explanation && (
                        <div className="sm-card p-5">
                            <h2 className="font-mono text-[10.5px] uppercase tracking-wider text-sm-text-muted mb-2">Why this was flagged</h2>
                            <p className="text-[13px] text-sm-text leading-relaxed">{conflict.explanation}</p>
                            {similarity !== null && (
                                <div className="mt-4 pt-4 border-t border-sm-border">
                                    <div className="flex items-center justify-between font-mono text-[10.5px] uppercase tracking-wider text-sm-text-muted mb-2">
                                        <span>Similarity</span>
                                        <span>{similarity}%</span>
                                    </div>
                                    <div className="h-1.5 rounded-full bg-sm-border/60 overflow-hidden" role="progressbar" aria-valuenow={similarity} aria-valuemin="0" aria-valuemax="100">
                                        <div className="h-full bg-sm-blue" style={{ width: `${similarity}%` }} />
                                    </div>
                                </div>
                            )}
                        </div>
                    )}
                </section>

                <aside className="space-y-4">
                    <section className="sm-card p-5">
                        <h3 className="text-[13px] font-semibold text-sm-text mb-4">Status Timeline</h3>
                        <div className="flex items-center">
                            {STAGES.map((stage, index) => {
                                const active = index <= currentStageIdx;
                                return (
                                    <React.Fragment key={stage}>
                                        <div className="flex flex-col items-center flex-1">
                                            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-[11px] font-semibold font-mono ${active ? "bg-sm-blue text-white" : "bg-sm-border/50 text-sm-text-muted"}`}>
                                                {active ? <CheckCircle2 className="w-4 h-4" /> : index + 1}
                                            </div>
                                            <span className={`mt-2 text-[10.5px] font-mono uppercase tracking-wider ${active ? "text-sm-text" : "text-sm-text-muted"}`}>
                                                {stage.replace("_", " ")}
                                            </span>
                                        </div>
                                        {index < STAGES.length - 1 && (
                                            <div className={`h-0.5 flex-1 ${index < currentStageIdx ? "bg-sm-blue" : "bg-sm-border"}`} />
                                        )}
                                    </React.Fragment>
                                );
                            })}
                        </div>
                    </section>

                    <section className="sm-card p-5">
                        <h3 className="text-[13px] font-semibold text-sm-text mb-4">Resolve</h3>
                        <div className="grid grid-cols-1 gap-2 mb-4">
                            {OPTIONS.map((option) => (
                                <button
                                    key={option.key}
                                    data-testid={`resolve-${option.key}`}
                                    onClick={() => { setSelected(option.key); setFieldErrors({}); }}
                                    title={option.hint}
                                    className={`h-10 px-4 rounded-lg text-[12.5px] font-medium transition-all active:scale-[0.97] flex items-center justify-between ${
                                        selected === option.key
                                            ? "border text-sm-text"
                                            : "bg-white/[0.03] border border-sm-border text-sm-text-secondary hover:text-sm-text"
                                    }`}
                                    style={selected === option.key ? { borderColor: option.color, background: `${option.color}18` } : {}}
                                >
                                    <span className="flex items-center gap-2.5">
                                        <span className="w-2 h-2 rounded-full" style={{ background: option.color }} />
                                        {option.label}
                                    </span>
                                    {selected === option.key && <CheckCircle2 className="w-4 h-4" style={{ color: option.color }} />}
                                </button>
                            ))}
                        </div>

                        {selected === "merged" && (
                            <div className="mb-4">
                                <label htmlFor="merged-content" className="font-mono text-[10.5px] uppercase tracking-wider text-sm-text-muted block mb-2">Merged content</label>
                                <Textarea
                                    id="merged-content"
                                    data-testid="merged-content"
                                    value={mergedContent}
                                    onChange={(event) => setMergedContent(event.target.value)}
                                    placeholder="The single statement that replaces both memories…"
                                    rows={5}
                                    className="bg-sm-bg/60 border-sm-border text-sm-text placeholder:text-sm-text-muted font-mono text-[12.5px] resize-none"
                                />
                                <FieldError message={fieldErrors.mergedContent} />
                            </div>
                        )}

                        {selected === "split" && (
                            <div className="mb-4 grid grid-cols-2 gap-2">
                                <div>
                                    <label htmlFor="tag-a" className="font-mono text-[10.5px] uppercase tracking-wider text-sm-text-muted block mb-2">Tag for A</label>
                                    <Input id="tag-a" data-testid="tag-a" value={tagA} onChange={(event) => setTagA(event.target.value)} placeholder="tag to add to A" className="bg-sm-bg/60 border-sm-border text-sm-text font-mono text-[12.5px] h-9" />
                                    <FieldError message={fieldErrors.tagA} />
                                </div>
                                <div>
                                    <label htmlFor="tag-b" className="font-mono text-[10.5px] uppercase tracking-wider text-sm-text-muted block mb-2">Tag for B</label>
                                    <Input id="tag-b" data-testid="tag-b" value={tagB} onChange={(event) => setTagB(event.target.value)} placeholder="tag to add to B" className="bg-sm-bg/60 border-sm-border text-sm-text font-mono text-[12.5px] h-9" />
                                    <FieldError message={fieldErrors.tagB} />
                                </div>
                            </div>
                        )}

                        {selected === "deferred" && (
                            <div className="mb-4">
                                <label htmlFor="revisit-at" className="font-mono text-[10.5px] uppercase tracking-wider text-sm-text-muted block mb-2">Revisit at</label>
                                <Input id="revisit-at" data-testid="revisit-at" type="datetime-local" value={revisitAt} onChange={(event) => setRevisitAt(event.target.value)} className="bg-sm-bg/60 border-sm-border text-sm-text font-mono text-[12.5px] h-9" />
                                <p className="text-[10.5px] text-sm-text-muted mt-1.5">Interpreted in your local timezone and stored as UTC.</p>
                                <FieldError message={fieldErrors.revisitAt} />
                            </div>
                        )}

                        <label htmlFor="resolve-note" className="font-mono text-[10.5px] uppercase tracking-wider text-sm-text-muted block mb-2">Resolution note</label>
                        <Textarea
                            id="resolve-note"
                            data-testid="resolve-note"
                            value={note}
                            onChange={(event) => setNote(event.target.value)}
                            placeholder="Why this resolution? (optional)"
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

function ExcerptCard({ label, memory, color }) {
    return (
        <div className="sm-card p-5">
            <div className="flex items-center justify-between gap-3 mb-3">
                <span className="font-mono text-[10.5px] uppercase tracking-wider" style={{ color }}>{label}</span>
                {memory?.id && <code className="font-mono text-[10.5px] text-sm-text-muted truncate select-all">{memory.id}</code>}
            </div>
            <p className="font-mono text-[12.5px] text-sm-text leading-relaxed">{memory?.content || "—"}</p>
        </div>
    );
}

function FieldError({ message }) {
    if (!message) return null;
    return <p role="alert" className="text-[11px] text-red-400 mt-1.5">{message}</p>;
}
