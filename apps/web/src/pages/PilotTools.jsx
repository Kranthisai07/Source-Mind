import React, { useEffect, useRef, useState } from "react";
import TopBar from "../components/layout/TopBar";
import { Button } from "../components/ui/button";
import { Textarea } from "../components/ui/textarea";
import api, { resolveCurrentWorkspace } from "../lib/api";
import { classifyApiError } from "../lib/apiError";
import { isPilotMode, isPilotOperatorRole } from "../lib/pilotMode";
import { createSubmissionKeyHolder, submissionIdentity } from "../lib/submissionKey";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SOURCES = ["independent", "self", "external"];

export function validatePilotRating(form) {
    if (!UUID_RE.test(form.searchEventId || "")) return "Search event ID must be a UUID.";
    if (!UUID_RE.test(form.memoryId || "")) return "Memory ID must be a UUID.";
    if (!SOURCES.includes(form.ratingSource)) return "Choose a rating source.";
    if (form.ratingSource === "external") {
        if (!form.raterPseudonym.trim()) return "External ratings require a pseudonym.";
    } else if (!UUID_RE.test(form.raterUserId || "")) {
        return "Self and independent ratings require a workspace rater.";
    }
    if (form.ratingKind === "allocation") {
        const allocation = Number(form.allocation);
        if (form.allocation === "" || !Number.isFinite(allocation) || allocation < 0 || allocation > 100) {
            return "Allocation must be between 0 and 100.";
        }
    } else if (form.ratingKind === "exclusion") {
        if (!form.exclusionReason.trim()) return "An exclusion reason is required.";
    } else {
        return "Choose allocation or exclusion.";
    }
    return null;
}

const initialForm = {
    searchEventId: "",
    memoryId: "",
    ratingSource: "independent",
    raterUserId: "",
    raterPseudonym: "",
    ratingKind: "allocation",
    allocation: "",
    exclusionReason: "",
};

export default function PilotTools() {
    const [access, setAccess] = useState({ loading: true, allowed: false, error: null });
    const [workspaceId, setWorkspaceId] = useState(null);
    const [members, setMembers] = useState([]);
    const [currentUserId, setCurrentUserId] = useState(null);
    const [form, setForm] = useState(initialForm);
    const [ratingState, setRatingState] = useState({ saving: false, submitted: false, error: null, success: null });
    const [exportState, setExportState] = useState({ saving: false, error: null, success: null });
    const ratingKeyHolder = useRef(createSubmissionKeyHolder());
    const ratingLocked = ratingState.saving || ratingState.submitted;

    useEffect(() => {
        let cancelled = false;
        if (!isPilotMode()) {
            setAccess({ loading: false, allowed: false, error: "Pilot mode is not enabled." });
            return () => { cancelled = true; };
        }
        Promise.all([resolveCurrentWorkspace(), api.getCurrentUser()])
            .then(async ([resolvedWorkspaceId, user]) => {
                const response = await api.listTeamMembers(resolvedWorkspaceId);
                if (cancelled) return;
                const workspaceMembers = Array.isArray(response?.members) ? response.members : [];
                const membership = workspaceMembers.find((item) => (item.user || item).id === user.id);
                setWorkspaceId(resolvedWorkspaceId);
                setMembers(workspaceMembers);
                setCurrentUserId(user.id);
                setAccess({
                    loading: false,
                    allowed: isPilotOperatorRole(membership?.role),
                    error: isPilotOperatorRole(membership?.role) ? null : "Owner or admin access is required.",
                });
            })
            .catch((requestError) => {
                if (cancelled) return;
                const classified = classifyApiError(requestError);
                setAccess({ loading: false, allowed: false, error: classified.detail || classified.title });
            });
        return () => { cancelled = true; };
    }, []);

    const updateForm = (field, value) => {
        if (ratingLocked) return;
        setForm((current) => ({ ...current, [field]: value }));
        setRatingState({ saving: false, submitted: false, error: null, success: null });
    };

    const startNewRating = () => {
        setRatingState({ saving: false, submitted: false, error: null, success: null });
    };

    const submitRating = async (event) => {
        event.preventDefault();
        if (ratingState.saving || ratingState.submitted) return;
        const validationError = validatePilotRating(form);
        if (validationError) {
            setRatingState({ saving: false, submitted: false, error: validationError, success: null });
            return;
        }
        const payload = {
            search_event_id: form.searchEventId.trim(),
            memory_id: form.memoryId.trim(),
            rating_source: form.ratingSource,
            rater_user_id: form.ratingSource === "external" ? null : form.raterUserId,
            rater_pseudonym: form.ratingSource === "external" ? form.raterPseudonym.trim() : null,
            allocation: form.ratingKind === "allocation" ? Number(form.allocation) : null,
            exclusion_reason: form.ratingKind === "exclusion" ? form.exclusionReason.trim() : null,
        };
        const idempotencyKey = ratingKeyHolder.current.keyFor(submissionIdentity({
            payload,
            workspaceId,
            userId: currentUserId,
        }));
        setRatingState({ saving: true, submitted: false, error: null, success: null });
        try {
            const rating = await api.createPilotRating({
                workspace_id: workspaceId,
                idempotencyKey,
                ...payload,
            });
            ratingKeyHolder.current.clear();
            setRatingState({ saving: false, submitted: true, error: null, success: `Rating ${rating.id} recorded.` });
        } catch (requestError) {
            const classified = classifyApiError(requestError);
            setRatingState({
                saving: false,
                submitted: false,
                error: requestError?.body?.error?.message || classified.detail || classified.title,
                success: null,
            });
        }
    };

    const downloadExport = async () => {
        setExportState({ saving: true, error: null, success: null });
        try {
            const result = await api.getPilotExport(workspaceId);
            const exportPayload = { ...result };
            delete exportPayload.__latency_ms;
            const blob = new Blob([JSON.stringify(exportPayload, null, 2)], { type: "application/json" });
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            link.download = `sourcemind-pilot-export-${workspaceId}.json`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);
            setExportState({ saving: false, error: null, success: "Pilot export downloaded." });
        } catch (requestError) {
            const classified = classifyApiError(requestError);
            setExportState({ saving: false, error: classified.detail || classified.title, success: null });
        }
    };

    if (access.loading) {
        return <><TopBar title="Pilot Tools" subtitle="Checking operator access" /><div className="p-8 text-sm-text-secondary">Checking access…</div></>;
    }
    if (!access.allowed) {
        return (
            <>
                <TopBar title="Pilot Tools" subtitle="Restricted operator workflow" />
                <div className="p-8">
                    <section data-testid="pilot-access-denied" className="sm-card p-8 max-w-xl">
                        <h2 className="text-[16px] font-semibold text-sm-text">Operator access required</h2>
                        <p className="mt-2 text-[13px] text-sm-text-secondary">{access.error}</p>
                    </section>
                </div>
            </>
        );
    }

    return (
        <>
            <TopBar title="Pilot Tools" subtitle="Restricted rating and export operations" />
            <div className="flex-1 px-8 py-6 grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_360px] gap-5">
                <form onSubmit={submitRating} className="sm-card p-6 space-y-5" data-testid="pilot-rating-form">
                    <div>
                        <h2 className="text-[15px] font-semibold text-sm-text">Record rating</h2>
                        <p className="mt-1 text-[12.5px] text-sm-text-secondary">Scores and candidate system names are intentionally absent.</p>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <Field label="Search event ID">
                            <TextInput testId="rating-search-event" value={form.searchEventId} onChange={(value) => updateForm("searchEventId", value)} disabled={ratingLocked} />
                        </Field>
                        <Field label="Memory ID">
                            <TextInput testId="rating-memory" value={form.memoryId} onChange={(value) => updateForm("memoryId", value)} disabled={ratingLocked} />
                        </Field>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <Field label="Rating source">
                            <select data-testid="rating-source" value={form.ratingSource} onChange={(event) => updateForm("ratingSource", event.target.value)} disabled={ratingLocked} className="pilot-input">
                                {SOURCES.map((source) => <option key={source} value={source}>{source}</option>)}
                            </select>
                        </Field>
                        {form.ratingSource === "external" ? (
                            <Field label="External pseudonym">
                                <TextInput testId="rating-pseudonym" value={form.raterPseudonym} onChange={(value) => updateForm("raterPseudonym", value)} disabled={ratingLocked} />
                            </Field>
                        ) : (
                            <Field label="Workspace rater">
                                <select data-testid="rating-rater" value={form.raterUserId} onChange={(event) => updateForm("raterUserId", event.target.value)} disabled={ratingLocked} className="pilot-input">
                                    <option value="">Choose a rater</option>
                                    {members.map((membership) => {
                                        const member = membership.user || membership;
                                        return <option key={member.id} value={member.id}>{member.display_name || member.id}</option>;
                                    })}
                                </select>
                            </Field>
                        )}
                    </div>
                    <Field label="Rating result">
                        <select data-testid="rating-kind" value={form.ratingKind} onChange={(event) => updateForm("ratingKind", event.target.value)} disabled={ratingLocked} className="pilot-input">
                            <option value="allocation">Allocation</option>
                            <option value="exclusion">Cannot judge / exclude</option>
                        </select>
                    </Field>
                    {form.ratingKind === "allocation" ? (
                        <Field label="Allocation (0–100)">
                            <input data-testid="rating-allocation" type="number" min="0" max="100" step="0.01" value={form.allocation} onChange={(event) => updateForm("allocation", event.target.value)} disabled={ratingLocked} className="pilot-input" />
                        </Field>
                    ) : (
                        <Field label="Exclusion reason">
                            <Textarea data-testid="rating-exclusion" value={form.exclusionReason} onChange={(event) => updateForm("exclusionReason", event.target.value)} disabled={ratingLocked} className="bg-sm-bg/60 border-sm-border text-sm-text" />
                        </Field>
                    )}
                    {ratingState.error && <p role="alert" data-testid="rating-error" className="text-[12px] text-sm-red">{ratingState.error}</p>}
                    {ratingState.success && <p data-testid="rating-success" className="text-[12px] text-sm-green">{ratingState.success}</p>}
                    <Button data-testid="rating-submit" type="submit" disabled={ratingLocked} className="bg-sm-blue text-white">
                        {ratingState.saving ? "Recording…" : ratingState.submitted ? "Rating recorded" : "Record rating"}
                    </Button>
                    {ratingState.submitted && (
                        <Button data-testid="rating-start-new" type="button" variant="outline" onClick={startNewRating}>
                            Start new or changed rating
                        </Button>
                    )}
                </form>

                <section className="sm-card p-6 h-fit" data-testid="pilot-export-panel">
                    <h2 className="text-[15px] font-semibold text-sm-text">Workspace export</h2>
                    <p className="mt-2 text-[12.5px] text-sm-text-secondary">Downloads the admin-scoped canonical pilot evidence JSON.</p>
                    <Button data-testid="pilot-export-download" onClick={downloadExport} disabled={exportState.saving} className="mt-5 bg-sm-blue text-white w-full">
                        {exportState.saving ? "Preparing…" : "Download pilot export"}
                    </Button>
                    {exportState.error && <p role="alert" className="mt-3 text-[12px] text-sm-red">{exportState.error}</p>}
                    {exportState.success && <p className="mt-3 text-[12px] text-sm-green">{exportState.success}</p>}
                </section>
            </div>
        </>
    );
}

function Field({ label, children }) {
    return <label className="block text-[12px] text-sm-text-secondary space-y-2"><span>{label}</span>{children}</label>;
}

function TextInput({ testId, value, onChange, disabled = false }) {
    return <input data-testid={testId} value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled} className="pilot-input" />;
}
