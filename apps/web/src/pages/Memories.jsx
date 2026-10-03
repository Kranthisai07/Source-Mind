import React, { useEffect, useRef, useState } from "react";
import { Search, Plus, X } from "lucide-react";
import TopBar from "../components/layout/TopBar";
import MemoryCard from "../components/widgets/MemoryCard";
import PipelineTracker from "../components/widgets/PipelineTracker";
import { Button } from "../components/ui/button";
import { Textarea } from "../components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "../components/ui/sheet";
import api, { resolveCurrentWorkspace } from "../lib/api";
import { classifyApiError } from "../lib/apiError";
import { createSubmissionKeyHolder, submissionIdentity } from "../lib/submissionKey";
import { getCurrentUserId } from "../lib/currentUser";
import { toast } from "sonner";

const MODES = ["hybrid", "semantic", "keyword"];
const CATEGORIES = ["general", "architecture", "decision", "incident", "onboarding"];

export default function Memories() {
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
            api.searchMemories({ query, mode, limit: 24 })
                .then((response) => {
                    if (cancelled) return;
                    setResults(Array.isArray(response?.results) ? response.results : []);
                    setTotal(response?.total_found ?? 0);
                    setLatency(response?.latency_ms ?? response?.__latency_ms ?? null);
                    setLoading(false);
                })
                .catch((requestError) => {
                    if (cancelled) return;
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
    }, [query, mode, reloadNonce]);

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

            <IngestPanel open={open} onOpenChange={setOpen} />
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

function IngestPanel({ open, onOpenChange }) {
    const [content, setContent] = useState("");
    const [tagsRaw, setTagsRaw] = useState("");
    const [tags, setTags] = useState([]);
    const [category, setCategory] = useState("general");
    const [jobId, setJobId] = useState(null);
    const [job, setJob] = useState(null);
    const [jobError, setJobError] = useState(null);
    const [retryNonce, setRetryNonce] = useState(0);
    const keyHolder = useRef(null);
    const inFlight = useRef(false);

    if (keyHolder.current === null) keyHolder.current = createSubmissionKeyHolder();

    useEffect(() => {
        if (!jobId) return;
        let cancelled = false;
        let timer = null;
        const owner = getCurrentUserId();

        const stop = () => {
            cancelled = true;
            if (timer) clearTimeout(timer);
        };

        const poll = async () => {
            while (!cancelled) {
                if (getCurrentUserId() !== owner) return stop();
                try {
                    const response = await api.getJobStatus(jobId);
                    if (cancelled) return;
                    setJob(response);
                    if (response.status === "done" || response.status === "failed") return stop();
                } catch (requestError) {
                    if (cancelled) return;
                    setJob(null);
                    setJobError(requestError?.body?.error?.message || classifyApiError(requestError).title);
                    return stop();
                }
                await new Promise((resolve) => { timer = setTimeout(resolve, 180); });
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
        inFlight.current = true;
        try {
            const workspaceId = await resolveCurrentWorkspace();
            const payload = { content, tags, category };
            const idempotencyKey = keyHolder.current.keyFor(submissionIdentity({
                payload,
                workspaceId,
                userId: getCurrentUserId(),
            }));
            const response = await api.createMemory({
                ...payload,
                workspace_id: workspaceId,
                idempotencyKey,
            });
            keyHolder.current.clear();
            setJobId(response.job_id);
        } catch (requestError) {
            const classified = classifyApiError(requestError);
            toast.error(classified.title, {
                description: requestError?.body?.error?.message || classified.detail,
            });
        } finally {
            inFlight.current = false;
        }
    };

    const failed = job?.status === "failed" || jobError != null;

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
    };

    return (
        <Sheet open={open} onOpenChange={(value) => { onOpenChange(value); if (!value) reset(); }}>
            <SheetContent
                side="right"
                className="sm:max-w-[480px] w-[480px] bg-sm-surface border-l border-sm-border text-sm-text p-0 overflow-y-auto"
                data-testid="ingest-panel"
            >
                <SheetHeader className="p-6 border-b border-sm-border">
                    <SheetTitle className="text-sm-text text-[17px] font-semibold">Ingest Knowledge</SheetTitle>
                    <SheetDescription className="text-sm-text-secondary text-[12.5px]">
                        Paste a document, meeting notes, or decision record. We'll extract, chunk, embed, and attribute.
                    </SheetDescription>
                </SheetHeader>

                {!jobId ? (
                    <div className="p-6 space-y-5">
                        <div>
                            <label htmlFor="ingest-content" className="block text-[12px] text-sm-text-secondary mb-2 font-medium">Content</label>
                            <Textarea
                                id="ingest-content"
                                data-testid="ingest-content"
                                value={content}
                                onChange={(event) => setContent(event.target.value)}
                                placeholder="Paste document content, meeting notes, decision records..."
                                rows={10}
                                className="bg-sm-bg/60 border-sm-border text-sm-text placeholder:text-sm-text-muted resize-none font-mono text-[12.5px]"
                            />
                        </div>

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

                        <Button
                            data-testid="ingest-submit"
                            onClick={submit}
                            disabled={!content.trim()}
                            className="w-full h-10 bg-sm-blue hover:bg-sm-blue/90 text-white disabled:opacity-40"
                        >
                            Ingest &amp; Process
                        </Button>
                    </div>
                ) : (
                    <div className="p-6 space-y-4">
                        <div className="flex items-center gap-2 text-[12.5px] text-sm-text-secondary">
                            {failed ? (
                                <span className="text-sm-red" data-testid="ingest-failed">
                                    {jobError
                                        ? `Could not read ingestion status · ${jobError}`
                                        : `Ingestion failed${job?.error ? ` · ${job.error}` : ""}`}
                                </span>
                            ) : job?.status === "done" ? (
                                <span className="text-sm-green">Pipeline complete · {job.elapsed_ms}ms total</span>
                            ) : (
                                <>Processing · stage: <span className="font-mono text-sm-blue">{job?.stage ?? "queued"}</span></>
                            )}
                        </div>
                        <PipelineTracker stages={job?.stages ?? []} currentStage={job?.stage} />
                        {jobError && (
                            <Button
                                onClick={() => { setJobError(null); setRetryNonce((value) => value + 1); }}
                                variant="outline"
                                data-testid="ingest-status-retry"
                                className="w-full mt-4 bg-white/[0.03] border-sm-border text-sm-text"
                            >
                                Check again
                            </Button>
                        )}
                        {(job?.status === "done" || failed) && (
                            <Button onClick={reset} variant="outline" data-testid="ingest-reset" className="w-full mt-4 bg-white/[0.03] border-sm-border text-sm-text hover:bg-white/[0.06]">
                                {failed ? "Start over" : "Ingest another"}
                            </Button>
                        )}
                    </div>
                )}
            </SheetContent>
        </Sheet>
    );
}
