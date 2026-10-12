import React, { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
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
import { getCurrentUserId, onCurrentUserChange } from "../lib/currentUser";
import { isStaleWorkspaceError } from "../lib/realApi";
import { carryDraft, clearCarriedDraft, clearDraft, loadCarriedDraft, loadDraft, saveDraft } from "../lib/editDrafts";
import { relativeTime, formatDate } from "../lib/format";
import { isPilotMode } from "../lib/pilotMode";
import { createSubmissionKeyHolder, submissionIdentity } from "../lib/submissionKey";

const NO_SINGLE_NEWER = {
    ambiguous_successor: "More than one newer version exists, so there is no single latest version to open.",
    no_current_version: "This memory currently has no current version, so there is nothing newer to open.",
    superseded_without_successor: "This version was retired and has no newer version to open.",
};

/**
 * Turn a failed PATCH into what the page must say. Only the error class
 * decides; nothing here infers whether the edit was applied.
 */
function classifySaveError(error) {
    const status = typeof error?.status === "number" ? error.status : null;
    const body = error?.body?.error;
    if (status === 409 && body?.code === "SM035") {
        const reason = body.details?.reason ?? null;
        return {
            kind: "conflict",
            reason,
            // Only a "superseded" reason names exactly one newer version.
            latestId: reason === "superseded" ? body.details?.latest_memory_id ?? null : null,
        };
    }
    if (status === 404) return { kind: "notfound" };
    if (error?.network === true || status === 0 || (status !== null && status >= 500)) {
        return { kind: "unknown" };
    }
    const classified = classifyApiError(error);
    return { kind: "other", message: body?.message || classified.detail || classified.title };
}

// History state outlives a sign-out (Back reaches it for the next person), so a
// carried draft in it counts only for the user who stamped it.
function stateCarried(state, userId) {
    const carried = state?.carriedDraft;
    return userId && carried && carried.userId === userId ? carried : null;
}

export default function MemoryDetail() {
    const { id } = useParams();
    // Keyed on the id: moving to another version starts with fresh page state
    // instead of carrying an open dialog or an error across.
    return <MemoryDetailView key={id} id={id} />;
}

function MemoryDetailView({ id }) {
    const navigate = useNavigate();
    const location = useLocation();
    const pilotMode = isPilotMode();
    // Subscribed, not snapshotted: on a hard refresh the auth bridge publishes
    // the user id after this component's first render.
    const userId = useSyncExternalStore(onCurrentUserChange, getCurrentUserId);
    const [editOpen, setEditOpen] = useState(false);
    // The unsaved draft, per (user, memory id). Component state is primary;
    // sessionStorage lets it survive a remount. null means "no draft".
    const [draft, setDraftState] = useState(() => loadDraft(userId, id));
    // A draft handed over from an older version; offered, never auto-applied.
    // Router state is the fallback transport when sessionStorage refuses writes.
    const [carried, setCarried] = useState(
        () => loadCarriedDraft(userId, id) ?? stateCarried(location.state, userId)
    );
    // True once the user has typed or accepted something in this page: such a
    // draft is never replaced by one reloaded after the identity settles.
    const dirty = useRef(false);
    const seenUser = useRef(userId);
    // History state as this page was entered (read once; effects use the ref).
    const entryState = useRef(location.state);
    const [editSaving, setEditSaving] = useState(false);
    const [saveError, setSaveError] = useState(null);
    const [history, setHistory] = useState(null);
    const [editSuccess, setEditSuccess] = useState(location.state?.editSuccess ?? null);
    const saveInFlight = useRef(false);
    // The caller's role in the current workspace, same lookup as Settings. Only
    // used to disable Edit for a viewer up front; unknown means "do not
    // disable", and the server's 403 stays the backstop.
    const [isViewer, setIsViewer] = useState(false);

    const memoryResource = useApiResource(() => api.getMemory(id), { resetKey: id });
    const memoryWorkspaceId = memoryResource.data?.workspace_id;
    const memoryLoaded = Boolean(memoryResource.data);
    useEffect(() => {
        // The role that counts is the one in the memory's OWN workspace, which
        // can differ from the selected one (deep links).
        if (!memoryLoaded) return undefined;
        let cancelled = false;
        (async () => {
            try {
                const [me, team] = await Promise.all([api.getCurrentUser(), api.listTeamMembers(memoryWorkspaceId)]);
                const members = Array.isArray(team?.members) ? team.members : [];
                const mine = me ? members.find((item) => (item.user || item).id === me.id) : null;
                if (!cancelled) setIsViewer(String(mine?.role || "").toLowerCase() === "viewer");
            } catch {
                // Role unknown: leave Edit enabled; the server still decides.
            }
        })();
        return () => { cancelled = true; };
    }, [userId, memoryLoaded, memoryWorkspaceId]);

    useEffect(() => {
        if (seenUser.current === userId) return;
        seenUser.current = userId;
        if (!dirty.current) setDraftState(loadDraft(userId, id));
        setCarried((current) => current ?? loadCarriedDraft(userId, id) ?? stateCarried(entryState.current, userId));
    }, [userId, id]);
    // Best effort only: the backend does not replay on this key, so reusing it
    // after an unknown outcome does not make a resend safe.
    const editKeyHolder = useRef(createSubmissionKeyHolder());
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

    const superseded = mem.is_current === false;
    const latestId = superseded ? mem.latest_memory_id ?? null : null;
    const contentValue = draft?.content ?? mem.content ?? "";
    const tagsValue = draft?.tags ?? tags.join(", ");

    const setDraft = (patch) => {
        dirty.current = true;
        const next = { content: contentValue, tags: tagsValue, ...patch };
        setDraftState(next);
        saveDraft(userId, id, next);
    };

    const discardDraft = () => {
        clearDraft(userId, id);
        setDraftState(null);
        setSaveError(null);
        setHistory(null);
    };

    const openEditor = () => {
        setEditSuccess(null);
        setEditOpen(true);
    };

    // Navigation only. The draft travels via sessionStorage and the target page
    // merely offers it; nothing is opened or sent from here.
    const viewLatest = (targetId) => {
        // Also passed in router state: if storage refuses the write the draft
        // still reaches the next page instead of dying with this one.
        const carriedDraft = draft && userId ? { content: draft.content, tags: draft.tags, fromId: id, userId } : null;
        if (carriedDraft) carryDraft(userId, id, targetId, draft);
        setEditOpen(false);
        navigate(`/memories/${targetId}`, carriedDraft ? { state: { carriedDraft } } : undefined);
    };

    // Router state outlives the in-memory offer (reload, back/forward), so
    // settling an offer also clears it from the current history entry.
    const settleCarried = () => {
        clearCarriedDraft(userId, id);
        if (carried?.fromId) clearDraft(userId, carried.fromId);
        setCarried(null);
        entryState.current = null;
        if (location.state?.carriedDraft) navigate(location.pathname, { replace: true, state: null });
    };

    // mode: "replace" (no draft here yet, or the user chose the carried one),
    // "append" (keep this version's draft and add the carried text below it).
    const openCarriedDraft = (mode = "replace") => {
        if (!carried) return;
        const next = mode === "append" && draft
            ? {
                content: `${draft.content}\n\n--- Draft carried from an earlier version ---\n\n${carried.content}`,
                tags: draft.tags,
            }
            : { content: carried.content, tags: carried.tags };
        dirty.current = true;
        setDraftState(next);
        saveDraft(userId, id, next);
        settleCarried();
        setEditSuccess(null);
        setEditOpen(true);
    };

    const discardCarriedDraft = () => settleCarried();

    // Reads only. Reports what the server holds and never decides whether the
    // unconfirmed save was applied.
    const checkHistory = async () => {
        setHistory({ loading: true });
        const [memoryResult, versionsResult] = await Promise.allSettled([
            api.getMemory(id),
            api.getMemoryVersions(id),
        ]);
        setHistory({
            loading: false,
            memory: memoryResult.status === "fulfilled" ? memoryResult.value : null,
            memoryFailed: memoryResult.status === "rejected",
            versions: versionsResult.status === "fulfilled" && Array.isArray(versionsResult.value?.versions)
                ? versionsResult.value.versions
                : null,
            versionsFailed: versionsResult.status === "rejected",
        });
    };

    const saveEdit = async () => {
        if (!contentValue.trim() || editSaving || saveInFlight.current) return;
        const nextTags = tagsValue
            .split(",")
            .map((tag) => tag.trim())
            .filter(Boolean);
        const payload = { content: contentValue, tags: nextTags };
        const idempotencyKey = editKeyHolder.current.keyFor(submissionIdentity({
            payload,
            workspaceId: mem.workspace_id,
            userId,
        }));
        saveInFlight.current = true;
        setEditSaving(true);
        setSaveError(null);
        setHistory(null);
        setEditSuccess(null);
        try {
            const updated = await api.updateMemory(memoryId, { ...payload, idempotencyKey });
            editKeyHolder.current.clear();
            clearDraft(userId, id);
            setDraftState(null);
            setEditOpen(false);
            const message = `Saved as version ${updated.version ?? "next"}.`;
            setEditSuccess(message);
            if (updated.id && updated.id !== id) {
                navigate(`/memories/${updated.id}`, { replace: true, state: { editSuccess: message } });
            } else {
                memoryResource.retry();
                versionsResource.retry();
            }
        } catch (requestError) {
            if (isStaleWorkspaceError(requestError)) return;
            setSaveError(classifySaveError(requestError));
        } finally {
            saveInFlight.current = false;
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
                        <Button variant="outline" size="sm" onClick={openEditor} disabled={superseded || isViewer} title={superseded ? "This version has been replaced; editing it would be rejected" : isViewer ? "You have read-only access to this workspace, so you can't edit memories" : undefined} data-testid="mem-edit" className="bg-white/[0.04] border-sm-border text-sm-text disabled:opacity-40 disabled:cursor-not-allowed"><Edit3 className="w-3.5 h-3.5" /> Edit</Button>
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
            {superseded && (
                <div data-testid="memory-superseded-banner" role="status" className="mx-8 mt-5 rounded-lg border border-sm-amber/30 bg-sm-amber/10 px-4 py-3 text-[12.5px] text-sm-amber">
                    <p className="font-medium">This version was replaced by a newer one.</p>
                    {latestId ? (
                        <Button size="sm" variant="outline" onClick={() => viewLatest(latestId)} data-testid="memory-superseded-view-latest" className="mt-2 bg-white/[0.03] border-sm-border text-sm-text">
                            View latest version
                        </Button>
                    ) : (
                        <p className="mt-1">{NO_SINGLE_NEWER[mem.supersession_reason] ?? "There is no single newer version to open."}</p>
                    )}
                </div>
            )}
            {carried && !superseded && (
                <div data-testid="memory-carried-draft" className="mx-8 mt-5 rounded-lg border border-sm-blue/30 bg-sm-blue/10 px-4 py-3 text-[12.5px] text-sm-text">
                    <p>You have an unsaved draft from an earlier version of this memory. It has not been sent anywhere.</p>
                    {draft ? (
                        <>
                            <p data-testid="memory-carried-conflict" className="mt-2 font-medium">
                                This version already has its own unsaved draft. Nothing is replaced unless you choose.
                            </p>
                            <div className="mt-2 grid gap-2 md:grid-cols-2">
                                <div>
                                    <p className="text-[11px] text-sm-text-secondary">Draft already on this version</p>
                                    <pre data-testid="memory-existing-draft-preview" className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap font-sans">{draft.content}</pre>
                                </div>
                                <div>
                                    <p className="text-[11px] text-sm-text-secondary">Draft carried from the earlier version</p>
                                    <pre data-testid="memory-carried-preview" className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap font-sans">{carried.content}</pre>
                                </div>
                            </div>
                            <div className="mt-2 flex flex-wrap gap-2">
                                <Button size="sm" onClick={discardCarriedDraft} data-testid="memory-carried-keep-mine" className="bg-sm-blue text-white">
                                    Keep this version's draft
                                </Button>
                                <Button size="sm" variant="outline" onClick={() => openCarriedDraft("append")} data-testid="memory-carried-append" className="bg-white/[0.03] border-sm-border text-sm-text">
                                    Keep both (add carried text below)
                                </Button>
                                <Button size="sm" variant="outline" onClick={() => openCarriedDraft("replace")} data-testid="memory-carried-use" className="bg-white/[0.03] border-sm-border text-sm-text">
                                    Replace it with the carried draft
                                </Button>
                            </div>
                        </>
                    ) : (
                        <div className="mt-2 flex gap-2">
                            <Button size="sm" onClick={() => openCarriedDraft("replace")} data-testid="memory-carried-open" className="bg-sm-blue text-white">
                                Open editor with my unsaved draft
                            </Button>
                            <Button size="sm" variant="outline" onClick={discardCarriedDraft} data-testid="memory-carried-discard" className="bg-white/[0.03] border-sm-border text-sm-text">
                                Discard draft
                            </Button>
                        </div>
                    )}
                </div>
            )}
            {draft && !editOpen && (
                <div data-testid="memory-draft-notice" className="mx-8 mt-5 rounded-lg border border-sm-border bg-white/[0.02] px-4 py-3 text-[12.5px] text-sm-text-secondary">
                    You have an unsaved edit draft for this version. It is kept in this browser tab only.
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
                        <SaveErrorBanner
                            error={saveError}
                            onViewLatest={viewLatest}
                            onCheckHistory={checkHistory}
                            history={history}
                        />
                        <div>
                            <label htmlFor="memory-edit-content" className="mb-2 block text-[12px] text-sm-text-secondary">Content</label>
                            <Textarea
                                id="memory-edit-content"
                                data-testid="memory-edit-content"
                                value={contentValue}
                                onChange={(event) => { setDraft({ content: event.target.value }); setSaveError(null); }}
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
                                value={tagsValue}
                                onChange={(event) => { setDraft({ tags: event.target.value }); setSaveError(null); }}
                                disabled={editSaving}
                                className="h-10 w-full rounded-md border border-sm-border bg-sm-bg/60 px-3 text-[12.5px] text-sm-text outline-none focus:border-sm-blue"
                            />
                        </div>
                    </div>
                    <DialogFooter>
                        {draft && (
                            <Button variant="ghost" onClick={discardDraft} disabled={editSaving} data-testid="memory-edit-discard" className="mr-auto text-sm-text-secondary">
                                Discard draft
                            </Button>
                        )}
                        <Button variant="outline" onClick={() => setEditOpen(false)} disabled={editSaving}>Cancel</Button>
                        <Button data-testid="memory-edit-save" onClick={saveEdit} disabled={!contentValue.trim() || editSaving} className="bg-sm-blue text-white">
                            {editSaving ? "Saving…" : "Save new version"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </>
    );
}

function SaveErrorBanner({ error, onViewLatest, onCheckHistory, history }) {
    if (!error) return null;
    const keptNote = "Your draft is kept.";
    if (error.kind === "conflict") {
        return (
            <div role="alert" data-testid="memory-edit-conflict" className="rounded-lg border border-sm-amber/30 bg-sm-amber/10 px-3 py-2 text-[12.5px] text-sm-amber">
                <p className="font-medium">This version was replaced by a newer one.</p>
                <p className="mt-1">Nothing was saved. {keptNote}</p>
                {error.latestId ? (
                    <Button size="sm" variant="outline" onClick={() => onViewLatest(error.latestId)} data-testid="memory-edit-view-latest" className="mt-2 bg-white/[0.03] border-sm-border text-sm-text">
                        View latest version
                    </Button>
                ) : (
                    <p className="mt-1">{NO_SINGLE_NEWER[error.reason] ?? "There is no single newer version to open."}</p>
                )}
            </div>
        );
    }
    if (error.kind === "notfound") {
        return (
            <p role="alert" data-testid="memory-edit-notfound" className="text-[12px] text-sm-red">
                This memory was not found, or you don't have access to it. {keptNote}
            </p>
        );
    }
    if (error.kind === "unknown") {
        return (
            <div role="alert" data-testid="memory-edit-unknown" className="rounded-lg border border-sm-amber/30 bg-sm-amber/10 px-3 py-2 text-[12.5px] text-sm-amber">
                <p>Save outcome unknown. Your draft is kept. We can't tell whether it was applied until you check.</p>
                <p data-testid="memory-edit-check-first" className="mt-1">Check history before saving again: saving again sends another edit, and the server does not recognise it as a repeat.</p>
                <Button size="sm" variant="outline" onClick={onCheckHistory} disabled={history?.loading} data-testid="memory-edit-check-history" className="mt-2 bg-white/[0.03] border-sm-border text-sm-text">
                    {history?.loading ? "Checking…" : "Check history"}
                </Button>
                {history && !history.loading && <HistoryFacts history={history} onViewLatest={onViewLatest} />}
            </div>
        );
    }
    return <p role="alert" data-testid="memory-edit-error" className="text-[12px] text-sm-red">{error.message}</p>;
}

// Facts only. Deliberately no comparison with the draft and no verdict: even a
// latest version whose text equals the draft could be someone else's identical
// edit, so the user decides what it means.
function HistoryFacts({ history, onViewLatest }) {
    const { memory, versions } = history;
    const latest = memory?.is_current === false ? memory.latest_memory_id : null;
    const current = (versions ?? []).find((version) => version.is_current);
    return (
        <div data-testid="memory-edit-history" className="mt-3 space-y-2 rounded-md border border-sm-border bg-sm-bg/60 p-3 text-sm-text">
            <p className="text-[12px] text-sm-text-secondary">What the server holds now. Nothing has been resubmitted.</p>
            {history.memoryFailed && <p className="text-[12px] text-sm-red">The memory could not be loaded.</p>}
            {history.versionsFailed && <p className="text-[12px] text-sm-red">The version history could not be loaded.</p>}
            {memory && (
                <p data-testid="memory-history-viewed" className="text-[12px]">
                    This page's version: {memory.version != null ? `v${memory.version}` : "unknown version"}
                    {memory.updated_at ? `, updated ${formatDate(memory.updated_at)}` : ""}
                    {memory.is_current === false ? ", no longer the current version" : ""}.
                </p>
            )}
            {latest && (
                <Button size="sm" variant="outline" onClick={() => onViewLatest(latest)} data-testid="memory-history-view-latest" className="bg-white/[0.03] border-sm-border text-sm-text">
                    View latest version
                </Button>
            )}
            {versions && (
                <p data-testid="memory-history-current" className="text-[12px]">
                    {current
                        ? `Current version: v${current.version}${current.created_at ? `, created ${formatDate(current.created_at)}` : ""}.`
                        : memory?.is_current === false
                            ? "This version is no longer current. The history list only goes back from this version, so a newer version is not in it."
                            : "The history list does not mark a current version."}
                </p>
            )}
            {versions && versions.length > 0 && (
                <ol className="space-y-2">
                    {versions.map((version, index) => (
                        <li key={version.id || `${version.version}-${index}`} data-testid={`memory-history-version-${version.version}`} className="text-[12px]">
                            <span className="font-mono text-sm-blue">v{version.version}</span>{" "}
                            <span className="text-sm-text-secondary">
                                {version.is_current ? "current" : "historical"}
                                {version.created_at ? `, ${formatDate(version.created_at)}` : ""}
                            </span>
                            <pre className="mt-1 whitespace-pre-wrap font-sans text-sm-text">{version.content || "No content recorded."}</pre>
                        </li>
                    ))}
                </ol>
            )}
        </div>
    );
}
