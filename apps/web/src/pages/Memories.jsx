import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Search, Plus, X } from "lucide-react";
import TopBar from "../components/layout/TopBar";
import MemoryCard from "../components/widgets/MemoryCard";
import { Button } from "../components/ui/button";
import { Textarea } from "../components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "../components/ui/sheet";
import api, { resolveCurrentWorkspace } from "../lib/api";
import { classifyApiError } from "../lib/apiError";
import { createSubmissionKeyHolder, submissionIdentity } from "../lib/submissionKey";
import { getCurrentUserId } from "../lib/currentUser";
import { isPilotMode } from "../lib/pilotMode";
import { INGEST_TIMING, ingestOutcome, isAmbiguousSubmitFailure, pollDelayMs, statusLabel } from "../lib/ingestOutcome";
import { isStaleWorkspaceError, workspaceGeneration } from "../lib/realApi";
import { toast } from "sonner";

const MODES = ["hybrid", "semantic", "keyword"];
const CATEGORIES = ["general", "architecture", "decision", "incident", "onboarding"];

export default function Memories() {
    const pilotMode = isPilotMode();
    const [query, setQuery] = useState("");
    const [mode, setMode] = useState("hybrid");
    const [results, setResults] = useState([]);
    const [total, setTotal] = useState(0);
    const [latency, setLatency] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [open, setOpen] = useState(false);
    const [reloadNonce, setReloadNonce] = useState(0);

    useEffect(() => {
        let cancelled = false;
        if (!query.trim()) {
            setResults([]);
            setTotal(0);
            setLatency(null);
            setError(null);
            setLoading(false);
            return () => { cancelled = true; };
        }

        setLoading(true);
        setError(null);
        const timer = setTimeout(() => {
            api.searchMemories({ query, mode, limit: 24, ...(pilotMode ? {} : { include_attribution: true }) })
                .then((response) => {
                    if (cancelled) return;
                    setResults(Array.isArray(response?.results) ? response.results : []);
                    setTotal(response?.total_found ?? 0);
                    setLatency(response?.latency_ms ?? response?.__latency_ms ?? null);
                    setLoading(false);
                })
                .catch((requestError) => {
                    // Dropped because the workspace changed: show nothing.
                    if (cancelled || isStaleWorkspaceError(requestError)) return;
                    setError(classifyApiError(requestError));
                    setResults([]);
                    setTotal(0);
                    setLatency(null);
                    setLoading(false);
                });
        }, 120);

        return () => {
            cancelled = true;
            clearTimeout(timer);
        };
    }, [query, mode, reloadNonce, pilotMode]);

    return (
        <>
            <TopBar
                title="Memories"
                subtitle="Your team's extracted knowledge, ranked by relevance"
                actions={
                    <Button
                        data-testid="ingest-open-btn"
                        onClick={() => setOpen(true)}
                        className="bg-sm-blue hover:bg-sm-blue/90 text-white h-9 shadow-[0_0_0_1px_rgba(79,126,255,0.4)_inset]"
                    >
                        <Plus className="w-4 h-4" /> Ingest
                    </Button>
                }
            />
            <div className="flex-1 px-8 py-6 space-y-5">
                <div className="sm-card p-1.5 flex items-center gap-2">
                    <div className="relative flex-1 flex items-center">
                        <Search className="w-4 h-4 absolute left-4 top-1/2 -translate-y-1/2 text-sm-text-muted pointer-events-none" />
                        <input
                            data-testid="memories-search-input"
                            type="text"
                            value={query}
                            onChange={(event) => setQuery(event.target.value)}
                            placeholder="Search your team's knowledge..."
                            aria-label="Search memories"
                            className="w-full h-11 pl-11 pr-9 bg-transparent text-[14px] text-sm-text placeholder:text-sm-text-muted outline-none"
                        />
                        {query && (
                            <button
                                data-testid="memories-search-clear"
                                onClick={() => setQuery("")}
                                aria-label="Clear search"
                                className="absolute right-3 top-1/2 -translate-y-1/2 text-sm-text-muted hover:text-sm-text"
                            >
                                <X className="w-4 h-4" />
                            </button>
                        )}
                    </div>
                    <div className="flex items-center gap-1 pr-2" role="group" aria-label="Search mode">
                        {MODES.map((searchMode) => (
                            <button
                                key={searchMode}
                                data-testid={`mode-${searchMode}`}
                                onClick={() => setMode(searchMode)}
                                aria-pressed={mode === searchMode}
                                className={`h-8 px-3 rounded-md text-[11.5px] font-mono tracking-wide transition-colors ${
                                    mode === searchMode
                                        ? "bg-sm-blue/15 text-sm-blue border border-sm-blue/30"
                                        : "text-sm-text-secondary hover:text-sm-text border border-transparent hover:bg-white/[0.03]"
                                }`}
                            >
                                {searchMode}
                            </button>
                        ))}
                    </div>
                </div>

                {error ? (
                    <SearchError error={error} onRetry={() => setReloadNonce((value) => value + 1)} />
                ) : loading ? (
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4" role="status" aria-label="Searching">
                        {Array.from({ length: 6 }).map((_, index) => (
                            <div key={index} className="sm-card p-5 h-[200px] shimmer rounded-xl" />
                        ))}
                    </div>
                ) : results.length === 0 ? (
                    <EmptyState query={query} onIngest={() => setOpen(true)} />
                ) : (
                    <>
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                            {results.map((result, index) => {
                                const memory = result?.memory ?? result;
                                return (
                                    <MemoryCard
                                        key={memory?.id || memory?.memory_id || index}
                                        memory={memory}
                                        score={result?.score}
                                        rank={result?.rank ?? index + 1}
                                        matchType={result?.match_type}
                                        pilotMode={pilotMode}
                                    />
                                );
                            })}
                        </div>
                        <div data-testid="results-meta" className="flex items-center justify-between pt-4 border-t border-sm-border">
                            <span className="font-mono text-[11.5px] text-sm-text-secondary">1–{results.length} of {total}</span>
                            {latency != null && <span className="font-mono text-[11.5px] text-sm-text-secondary">{Math.round(latency)} ms</span>}
                        </div>
                    </>
                )}
            </div>

            <IngestPanel open={open} onOpenChange={setOpen} pilotMode={pilotMode} />
        </>
    );
}

function SearchError({ error, onRetry }) {
    return (
        <div className="sm-card py-20 flex flex-col items-center text-center" data-testid="error-state">
            <div className="w-12 h-12 rounded-xl bg-sm-red/10 border border-sm-red/20 flex items-center justify-center mb-4 text-sm-red">!</div>
            <h3 className="text-[16px] font-semibold text-sm-text mb-2">{error.title}</h3>
            <p className="text-[13px] text-sm-text-secondary max-w-md mb-5">{error.detail}</p>
            <Button data-testid="error-retry" onClick={onRetry} variant="outline" className="border-sm-border text-sm-text">
                Try again
            </Button>
        </div>
    );
}

function EmptyState({ query, onIngest }) {
    const hasQuery = Boolean(query.trim());
    return (
        <div className="sm-card py-24 flex flex-col items-center text-center" data-testid="memories-empty">
            <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-sm-blue/20 to-sm-purple/20 border border-sm-border flex items-center justify-center mb-5">
                <Search className="w-7 h-7 text-sm-blue" strokeWidth={1.8} />
            </div>
            <h3 className="text-[16px] font-semibold text-sm-text mb-2">{hasQuery ? "No matches yet" : "Type to search memories"}</h3>
            <p className="text-[13px] text-sm-text-secondary max-w-sm mb-6">
                {hasQuery
                    ? "Try different wording, or switch the search mode to broaden the match."
                    : "Search your team's extracted knowledge, or ingest a document, meeting note or decision record to add to it."}
            </p>
            <Button onClick={onIngest} className="bg-sm-blue hover:bg-sm-blue/90 text-white h-9">
                <Plus className="w-4 h-4" /> Ingest a document
            </Button>
        </div>
    );
}

function IngestPanel({ open, onOpenChange, pilotMode }) {
    const [content, setContent] = useState("");
    const [tagsRaw, setTagsRaw] = useState("");
    const [tags, setTags] = useState([]);
    const [category, setCategory] = useState("general");
    const [jobId, setJobId] = useState(null);
    const [job, setJob] = useState(null);
    const [jobError, setJobError] = useState(null);
    const [retryNonce, setRetryNonce] = useState(0);
    // The API said the POST itself matched an earlier submission.
    const [duplicateNotice, setDuplicateNotice] = useState(null);
    // The POST's `already_exists`: true = an earlier submission's job was
    // returned, false = new work, null = the API did not say.
    const [alreadyExists, setAlreadyExists] = useState(null);
    // Set when the POST may or may not have been received (no job id came back).
    const [submitFailure, setSubmitFailure] = useState(null);
    // The exact text of the last submission that reached a terminal outcome
    // that resubmitting cannot improve. The same text dedups to the same record.
    const [blockedContent, setBlockedContent] = useState(null);
    const [slow, setSlow] = useState(false);
    const [timedOut, setTimedOut] = useState(false);
    const [elapsedMs, setElapsedMs] = useState(null);
    const keyHolder = useRef(null);
    const inFlight = useRef(false);
    const startedAt = useRef(null);
    const submittedContent = useRef("");

    if (keyHolder.current === null) keyHolder.current = createSubmissionKeyHolder();

    useEffect(() => {
        if (!jobId) return;
        let cancelled = false;
        let timer = null;
        const owner = getCurrentUserId();
        // The workspace this job belongs to. A switch ends the poll; PR3's
        // generation guard is what makes "ignore the old workspace" certain.
        const scope = workspaceGeneration();
        const pollStart = performance.now();
        let attempt = 0;

        const stop = () => {
            cancelled = true;
            if (timer) clearTimeout(timer);
        };

        const poll = async () => {
            while (!cancelled) {
                if (getCurrentUserId() !== owner || scope !== workspaceGeneration()) return stop();
                try {
                    const response = await api.getJobStatus(jobId);
                    if (cancelled || scope !== workspaceGeneration()) return stop();
                    setJob(response);
                    if (ingestOutcome(response).terminal) {
                        setElapsedMs(Math.round(performance.now() - (startedAt.current ?? pollStart)));
                        return stop();
                    }
                } catch (requestError) {
                    // A workspace switch ends the poll silently.
                    if (cancelled || isStaleWorkspaceError(requestError)) return stop();
                    // A failed status REQUEST says nothing about the job.
                    setJob(null);
                    setJobError(requestError?.body?.error?.message || classifyApiError(requestError).title);
                    return stop();
                }
                const waited = performance.now() - pollStart;
                if (waited >= INGEST_TIMING.giveUpAfterMs) {
                    setTimedOut(true);
                    return stop();
                }
                if (waited >= INGEST_TIMING.slowAfterMs) setSlow(true);
                const delay = pollDelayMs(attempt);
                attempt += 1;
                await new Promise((resolve) => { timer = setTimeout(resolve, delay); });
            }
        };

        poll();
        return stop;
    }, [jobId, retryNonce]);

    const addTag = (event) => {
        if (event.key === "Enter" && tagsRaw.trim()) {
            event.preventDefault();
            setTags([...tags, tagsRaw.trim()]);
            setTagsRaw("");
        } else if (event.key === "Backspace" && !tagsRaw && tags.length) {
            setTags(tags.slice(0, -1));
        }
    };

    const submit = async () => {
        if (!content.trim() || inFlight.current) return;
        if (content.trim() === blockedContent) return;
        inFlight.current = true;
        setSubmitFailure(null);
        // The workspace this submission belongs to. If the user switches
        // before it settles, its outcome is not theirs to see in the new one.
        const scope = workspaceGeneration();
        try {
            const workspaceId = await resolveCurrentWorkspace();
            const payload = {
                content,
                tags,
                category,
                ...(pilotMode ? { ingestion_mode: "verbatim" } : {}),
            };
            // Kept across a failed POST on purpose: if the request did reach
            // the server, the retry carries the same key. Best effort - the
            // backend does not replay on this key, it dedups on the content.
            const idempotencyKey = keyHolder.current.keyFor(submissionIdentity({
                payload,
                workspaceId,
                userId: getCurrentUserId(),
            }));
            startedAt.current = performance.now();
            const response = await api.createMemory({
                ...payload,
                workspace_id: workspaceId,
                idempotencyKey,
            });
            keyHolder.current.clear();
            if (scope !== workspaceGeneration()) return;
            submittedContent.current = content;
            const existing = response?.already_exists === true;
            setAlreadyExists(typeof response?.already_exists === "boolean" ? response.already_exists : null);
            setDuplicateNotice(existing || response?.status === "duplicate" ? response : null);
            setJobId(response.job_id);
        } catch (requestError) {
            if (isStaleWorkspaceError(requestError) || scope !== workspaceGeneration()) return;
            const classified = classifyApiError(requestError);
            const message = requestError?.body?.error?.message || classified.detail;
            if (isAmbiguousSubmitFailure(requestError)) {
                setSubmitFailure({ ambiguous: true, message });
            }
            toast.error(classified.title, { description: message });
        } finally {
            inFlight.current = false;
        }
    };

    const outcome = job ? ingestOutcome(job) : null;
    const checkAgain = () => {
        setJobError(null);
        setTimedOut(false);
        setRetryNonce((value) => value + 1);
    };

    const reset = () => {
        keyHolder.current?.clear();
        setContent("");
        setTagsRaw("");
        setTags([]);
        setCategory("general");
        setJobId(null);
        setJob(null);
        setJobError(null);
        setRetryNonce(0);
        setDuplicateNotice(null);
        setAlreadyExists(null);
        setSubmitFailure(null);
        setBlockedContent(null);
        setSlow(false);
        setTimedOut(false);
        setElapsedMs(null);
    };

    // Back to the form with the text kept, but the same text blocked: it would
    // only return this same record.
    const editAndSubmitAgain = () => {
        keyHolder.current?.clear();
        // Trimmed: the API strips surrounding whitespace before hashing, so a
        // whitespace-only edit is the same document.
        setBlockedContent(submittedContent.current.trim());
        setJobId(null);
        setJob(null);
        setJobError(null);
        setRetryNonce(0);
        setDuplicateNotice(null);
        setAlreadyExists(null);
        setSlow(false);
        setTimedOut(false);
        setElapsedMs(null);
    };

    const sameTextBlocked = blockedContent !== null && content.trim() === blockedContent;
    const elapsedText = elapsedMs != null ? ` Measured in your browser: ${(elapsedMs / 1000).toFixed(1)} s from submit.` : "";

    return (
        <Sheet open={open} onOpenChange={(value) => { onOpenChange(value); if (!value) reset(); }}>
            <SheetContent
                side="right"
                className="sm:max-w-[480px] w-[480px] bg-sm-surface border-l border-sm-border text-sm-text p-0 overflow-y-auto"
                data-testid="ingest-panel"
            >
                <SheetHeader className="p-6 border-b border-sm-border">
                    <SheetTitle className="text-sm-text text-[17px] font-semibold">
                        {pilotMode ? "Create Pilot Memory" : "Ingest Knowledge"}
                    </SheetTitle>
                    <SheetDescription className="text-sm-text-secondary text-[12.5px]">
                        {pilotMode
                            ? "Verbatim mode stores this text as one memory without fact extraction."
                            : "Paste a document, meeting notes, or decision record. SourceMind processes it into searchable facts."}
                    </SheetDescription>
                </SheetHeader>

                {!jobId ? (
                    <div className="p-6 space-y-5">
                        {!pilotMode && (
                            <p data-testid="ingest-explainer" className="rounded-lg border border-sm-border bg-white/[0.02] px-3 py-2 text-[12px] leading-relaxed text-sm-text-secondary">
                                Your text is processed into searchable facts; it is not stored word for word.
                                One submission may produce several memories, one, or none.
                            </p>
                        )}
                        <div>
                            <label htmlFor="ingest-content" className="block text-[12px] text-sm-text-secondary mb-2 font-medium">Content</label>
                            <Textarea
                                id="ingest-content"
                                data-testid="ingest-content"
                                value={content}
                                onChange={(event) => { setContent(event.target.value); setSubmitFailure(null); }}
                                placeholder="Paste document content, meeting notes, decision records..."
                                rows={10}
                                className="bg-sm-bg/60 border-sm-border text-sm-text placeholder:text-sm-text-muted resize-none font-mono text-[12.5px]"
                            />
                        </div>

                        {pilotMode && (
                            <div data-testid="pilot-verbatim-notice" className="rounded-lg border border-sm-blue/30 bg-sm-blue/10 px-3 py-2 text-[12px] text-sm-blue">
                                Pilot mode is active · submission mode: verbatim
                            </div>
                        )}

                        <div>
                            <label htmlFor="ingest-tags" className="block text-[12px] text-sm-text-secondary mb-2 font-medium">Tags</label>
                            <div className="min-h-[44px] flex flex-wrap gap-1.5 p-2 rounded-lg bg-sm-bg/60 border border-sm-border">
                                {tags.map((tag, index) => (
                                    <span key={tag} className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-sm-blue/15 border border-sm-blue/30 text-sm-blue text-[11px] font-mono">
                                        {tag}
                                        <button onClick={() => setTags(tags.filter((_, itemIndex) => itemIndex !== index))} aria-label={`Remove tag ${tag}`}>
                                            <X className="w-3 h-3" />
                                        </button>
                                    </span>
                                ))}
                                <input
                                    id="ingest-tags"
                                    data-testid="ingest-tags"
                                    value={tagsRaw}
                                    onChange={(event) => setTagsRaw(event.target.value)}
                                    onKeyDown={addTag}
                                    placeholder={tags.length ? "" : "type and press enter"}
                                    className="flex-1 min-w-[120px] bg-transparent outline-none text-[12.5px] text-sm-text font-mono placeholder:text-sm-text-muted"
                                />
                            </div>
                        </div>

                        <div>
                            <label className="block text-[12px] text-sm-text-secondary mb-2 font-medium">Category</label>
                            <Select value={category} onValueChange={setCategory}>
                                <SelectTrigger data-testid="ingest-category" className="bg-sm-bg/60 border-sm-border text-sm-text">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent className="bg-sm-surface border-sm-border text-sm-text">
                                    {CATEGORIES.map((item) => (
                                        <SelectItem key={item} value={item} className="text-sm-text focus:bg-white/5 focus:text-sm-text">{item}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        {submitFailure?.ambiguous && (
                            <div role="alert" data-testid="ingest-submit-unconfirmed" className="rounded-lg border border-sm-amber/30 bg-sm-amber/10 px-3 py-2 text-[12px] text-sm-amber">
                                <p>We could not confirm that your submission was received. {submitFailure.message}</p>
                                <Button
                                    onClick={submit}
                                    variant="outline"
                                    data-testid="ingest-retry-submit"
                                    className="mt-2 h-8 bg-white/[0.03] border-sm-border text-sm-text"
                                >
                                    Retry
                                </Button>
                            </div>
                        )}

                        {sameTextBlocked && (
                            <p data-testid="ingest-same-text-note" className="text-[12px] text-sm-text-secondary">
                                This is the same text as your last submission, and identical text returns the same result. Edit the text to submit again.
                            </p>
                        )}

                        <Button
                            data-testid="ingest-submit"
                            onClick={submit}
                            disabled={!content.trim() || sameTextBlocked}
                            className="w-full h-10 bg-sm-blue hover:bg-sm-blue/90 text-white disabled:opacity-40"
                        >
                            {pilotMode ? "Create Verbatim Memory" : "Ingest & Process"}
                        </Button>
                    </div>
                ) : (
                    <div className="p-6 space-y-4" aria-live="polite">
                        {duplicateNotice && (
                            <div data-testid="ingest-duplicate" className="rounded-lg border border-sm-amber/30 bg-sm-amber/10 px-3 py-2 text-[12.5px] text-sm-amber">
                                <p className="font-medium">
                                    This exact text was already submitted{alreadyExists ? " — here are the memories from that earlier submission." : "."}
                                </p>
                                {duplicateNotice.message && <p className="mt-1">{duplicateNotice.message}</p>}
                            </div>
                        )}

                        {jobError ? (
                            <div data-testid="ingest-poll-error" className="space-y-3">
                                <p className="text-[12.5px] text-sm-amber">
                                    Could not read the processing status ({jobError}). This does not mean ingestion failed; it may still be running.
                                </p>
                                <Button
                                    onClick={checkAgain}
                                    variant="outline"
                                    data-testid="ingest-status-retry"
                                    className="w-full bg-white/[0.03] border-sm-border text-sm-text"
                                >
                                    Check again
                                </Button>
                            </div>
                        ) : outcome?.kind === "success" ? (
                            <div data-testid="ingest-success" className="space-y-3">
                                <p className="text-[13px] font-medium text-sm-green">
                                    {alreadyExists === false
                                        ? `Created ${outcome.count} ${outcome.count === 1 ? "memory" : "memories"}`
                                        : alreadyExists === true
                                            ? `${outcome.count} ${outcome.count === 1 ? "memory" : "memories"} from the earlier submission`
                                            : `Processed — ${outcome.count} ${outcome.count === 1 ? "memory" : "memories"}`}
                                </p>
                                <ul data-testid="ingest-memory-links" className="space-y-1.5">
                                    {outcome.ids.map((memoryId, index) => (
                                        <li key={memoryId}>
                                            <Link
                                                to={`/memories/${memoryId}`}
                                                onClick={() => onOpenChange(false)}
                                                data-testid={`ingest-memory-link-${index}`}
                                                className="font-mono text-[12px] text-sm-blue underline-offset-2 hover:underline"
                                            >
                                                Open memory {index + 1} ({memoryId.slice(0, 8)})
                                            </Link>
                                        </li>
                                    ))}
                                </ul>
                                <p data-testid="ingest-elapsed" className="text-[11.5px] text-sm-text-secondary">{elapsedText.trim()}</p>
                                <Button onClick={reset} variant="outline" data-testid="ingest-reset" className="w-full bg-white/[0.03] border-sm-border text-sm-text hover:bg-white/[0.06]">
                                    Ingest another
                                </Button>
                            </div>
                        ) : outcome?.kind === "empty" ? (
                            <div data-testid="ingest-empty" role="status" className="space-y-3">
                                <p className="rounded-lg border border-sm-amber/30 bg-sm-amber/10 px-3 py-2 text-[12.5px] text-sm-amber">
                                    Processing finished but no searchable memory was created.
                                    {job?.message ? ` ${job.message}` : ""}
                                </p>
                                <p data-testid="ingest-elapsed" className="text-[11.5px] text-sm-text-secondary">{elapsedText.trim()}</p>
                                <EditAgain onClick={editAndSubmitAgain} />
                            </div>
                        ) : outcome?.kind === "failed" ? (
                            <div className="space-y-3">
                                <p className="text-[12.5px] text-sm-red" data-testid="ingest-failed">
                                    Ingestion failed{job?.error || job?.message ? ` · ${job.error || job.message}` : ""}
                                </p>
                                <p data-testid="ingest-elapsed" className="text-[11.5px] text-sm-text-secondary">{elapsedText.trim()}</p>
                                <EditAgain onClick={editAndSubmitAgain} />
                            </div>
                        ) : outcome?.kind === "duplicate" ? (
                            <div className="space-y-3">
                                {!duplicateNotice && (
                                    <div data-testid="ingest-duplicate" className="rounded-lg border border-sm-amber/30 bg-sm-amber/10 px-3 py-2 text-[12.5px] text-sm-amber">
                                        <p className="font-medium">This exact text was already submitted.</p>
                                        {job?.message && <p className="mt-1">{job.message}</p>}
                                    </div>
                                )}
                                {outcome.ids.length > 0 && (
                                    <ul className="space-y-1.5">
                                        {outcome.ids.map((memoryId, index) => (
                                            <li key={memoryId}>
                                                <Link to={`/memories/${memoryId}`} onClick={() => onOpenChange(false)} className="font-mono text-[12px] text-sm-blue hover:underline">
                                                    Open existing memory {index + 1} ({memoryId.slice(0, 8)})
                                                </Link>
                                            </li>
                                        ))}
                                    </ul>
                                )}
                                <EditAgain onClick={editAndSubmitAgain} />
                            </div>
                        ) : timedOut ? (
                            <div data-testid="ingest-timeout" className="space-y-3">
                                <p className="text-[12.5px] text-sm-amber">
                                    This is taking longer than expected. It has not failed as far as we know, and it may still finish. You can leave this page and come back, or check again.
                                </p>
                                <Button onClick={checkAgain} variant="outline" data-testid="ingest-status-retry" className="w-full bg-white/[0.03] border-sm-border text-sm-text">
                                    Check again
                                </Button>
                            </div>
                        ) : (
                            <div className="space-y-2" data-testid="ingest-progress">
                                <div className="flex items-center gap-2 text-[12.5px] text-sm-text-secondary">
                                    <span>Processing · </span>
                                    <span data-testid="ingest-status-label" className={`font-mono ${outcome?.kind === "retrying" ? "text-sm-amber" : "text-sm-blue"}`}>
                                        {statusLabel(job?.status ?? "queued")}
                                    </span>
                                </div>
                                {slow && (
                                    <p data-testid="ingest-slow" className="text-[12px] text-sm-text-secondary">
                                        Still processing. You can leave this page; this does not mean anything has gone wrong.
                                    </p>
                                )}
                            </div>
                        )}
                    </div>
                )}
            </SheetContent>
        </Sheet>
    );
}

function EditAgain({ onClick }) {
    return (
        <div className="space-y-1.5">
            <Button onClick={onClick} variant="outline" data-testid="ingest-edit-again" className="w-full bg-white/[0.03] border-sm-border text-sm-text hover:bg-white/[0.06]">
                Edit text and submit again
            </Button>
            <p className="text-[11.5px] text-sm-text-secondary">Submitting the same text again returns this same result.</p>
        </div>
    );
}
