import React, { useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Edit3, Trash2, AlertTriangle, Share2 } from "lucide-react";
import { PieChart, Pie, Cell, ResponsiveContainer } from "recharts";
import TopBar from "../components/layout/TopBar";
import ContributorAvatar from "../components/widgets/ContributorAvatar";
import Markdown from "../components/widgets/Markdown";
import { PageLoadError } from "../components/widgets/PageLoadState";
import { Button } from "../components/ui/button";
import { Textarea } from "../components/ui/textarea";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "../components/ui/dialog";
import useApiResource from "../hooks/useApiResource";
import api from "../lib/api";
import { classifyApiError } from "../lib/apiError";
import { getCurrentUserId } from "../lib/currentUser";
import { relativeTime, formatDate } from "../lib/format";
import { isPilotMode } from "../lib/pilotMode";
import { createSubmissionKeyHolder, submissionIdentity } from "../lib/submissionKey";

export default function MemoryDetail() {
    const { id } = useParams();
    const navigate = useNavigate();
    const pilotMode = isPilotMode();
    const [editOpen, setEditOpen] = useState(false);
    const [draftContent, setDraftContent] = useState("");
    const [draftTags, setDraftTags] = useState("");
    const [editSaving, setEditSaving] = useState(false);
    const [editError, setEditError] = useState(null);
    const [editSuccess, setEditSuccess] = useState(null);
    const editKeyHolder = useRef(createSubmissionKeyHolder());
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

    const openEditor = () => {
        setDraftContent(mem.content || "");
        setDraftTags(tags.join(", "));
        setEditError(null);
        setEditSuccess(null);
        setEditOpen(true);
    };

    const saveEdit = async () => {
        if (!draftContent.trim() || editSaving) return;
        const nextTags = draftTags
            .split(",")
            .map((tag) => tag.trim())
            .filter(Boolean);
        const payload = { content: draftContent, tags: nextTags };
        const idempotencyKey = editKeyHolder.current.keyFor(submissionIdentity({
            payload,
            workspaceId: mem.workspace_id,
            userId: getCurrentUserId(),
        }));
        setEditSaving(true);
        setEditError(null);
        setEditSuccess(null);
        try {
            const updated = await api.updateMemory(memoryId, { ...payload, idempotencyKey });
            editKeyHolder.current.clear();
            setEditOpen(false);
            setEditSuccess(`Saved as version ${updated.version ?? "next"}.`);
            if (updated.id && updated.id !== id) {
                navigate(`/memories/${updated.id}`, { replace: true });
            } else {
                memoryResource.retry();
                versionsResource.retry();
            }
        } catch (requestError) {
            const classified = classifyApiError(requestError);
            setEditError(requestError?.body?.error?.message || classified.detail || classified.title);
        } finally {
            setEditSaving(false);
        }
    };

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
                        <Button variant="outline" size="sm" onClick={openEditor} data-testid="mem-edit" className="bg-white/[0.04] border-sm-border text-sm-text"><Edit3 className="w-3.5 h-3.5" /> Edit</Button>
                        <Button variant="outline" size="sm" disabled data-testid="mem-share" title="Sharing a memory isn't available on this screen yet" className="bg-white/[0.04] border-sm-border text-sm-text disabled:opacity-40 disabled:cursor-not-allowed"><Share2 className="w-3.5 h-3.5" /> Share</Button>
                        <Button variant="outline" size="sm" disabled data-testid="mem-delete" title="Deleting a memory isn't available on this screen yet" className="bg-white/[0.04] border-sm-border text-sm-red hover:text-sm-red disabled:opacity-40 disabled:cursor-not-allowed"><Trash2 className="w-3.5 h-3.5" /> Delete</Button>
                    </>
                }
            />
            {editSuccess && (
                <div data-testid="memory-edit-success" className="mx-8 mt-5 rounded-lg border border-sm-green/30 bg-sm-green/10 px-4 py-3 text-[12.5px] text-sm-green">
                    {editSuccess}
                </div>
            )}
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
                        {!pilotMode ? (
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
                        ) : (
                            <div data-testid="pilot-memory-attribution-hidden" className="text-[12px] text-sm-text-secondary">
                                Attribution and contribution scores are hidden during pilot ratings.
                            </div>
                        )}
                        <div className="text-right">
                            <div className="font-mono text-[11px] text-sm-text-secondary">created</div>
                            <div className="font-mono text-[12px] text-sm-text">{formatDate(mem.created_at)}</div>
                        </div>
                    </div>

                    {/* Attribution details */}
                    {!pilotMode && <div className="mt-8">
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
                    </div>}
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

                    {!pilotMode && <Button variant="outline" size="sm" className="w-full mt-6 bg-white/[0.03] border-sm-border text-sm-amber hover:bg-sm-amber/10" onClick={() => navigate("/conflicts")}>
                        <AlertTriangle className="w-3.5 h-3.5" /> View Conflicts
                    </Button>}
                </aside>
            </div>
            <Dialog open={editOpen} onOpenChange={(open) => { if (!editSaving) setEditOpen(open); }}>
                <DialogContent className="bg-sm-surface border-sm-border text-sm-text" data-testid="memory-edit-dialog">
                    <DialogHeader>
                        <DialogTitle>Edit memory</DialogTitle>
                        <DialogDescription className="text-sm-text-secondary">
                            Saving creates the next chronological memory version.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4">
                        <div>
                            <label htmlFor="memory-edit-content" className="mb-2 block text-[12px] text-sm-text-secondary">Content</label>
                            <Textarea
                                id="memory-edit-content"
                                data-testid="memory-edit-content"
                                value={draftContent}
                                onChange={(event) => { setDraftContent(event.target.value); setEditError(null); }}
                                disabled={editSaving}
                                rows={12}
                                className="bg-sm-bg/60 border-sm-border text-sm-text font-mono text-[12.5px]"
                            />
                        </div>
                        <div>
                            <label htmlFor="memory-edit-tags" className="mb-2 block text-[12px] text-sm-text-secondary">Tags, comma separated</label>
                            <input
                                id="memory-edit-tags"
                                data-testid="memory-edit-tags"
                                value={draftTags}
                                onChange={(event) => { setDraftTags(event.target.value); setEditError(null); }}
                                disabled={editSaving}
                                className="h-10 w-full rounded-md border border-sm-border bg-sm-bg/60 px-3 text-[12.5px] text-sm-text outline-none focus:border-sm-blue"
                            />
                        </div>
                        {editError && <p role="alert" data-testid="memory-edit-error" className="text-[12px] text-sm-red">{editError}</p>}
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setEditOpen(false)} disabled={editSaving}>Cancel</Button>
                        <Button data-testid="memory-edit-save" onClick={saveEdit} disabled={!draftContent.trim() || editSaving} className="bg-sm-blue text-white">
                            {editSaving ? "Saving…" : "Save new version"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </>
    );
}
