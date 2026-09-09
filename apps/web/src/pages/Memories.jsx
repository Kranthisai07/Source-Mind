import React, { useEffect, useState } from "react";
import { Search, Plus, X, Brain } from "lucide-react";
import PageHeader from "../components/ui-kit/PageHeader";
import EmptyState from "../components/ui-kit/EmptyState";
import { Skeleton } from "../components/ui-kit/Skeleton";
import MemoryCard from "../components/widgets/MemoryCard";
import PipelineTracker from "../components/widgets/PipelineTracker";
import { Button } from "../components/ui/button";
import { Textarea } from "../components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "../components/ui/sheet";
import api from "../lib/api";

/**
 * Page 3 of the Supermemory-console redesign, modelled on §4.2 (Documents):
 * toolbar row (search + filters + right-aligned primary button), then the
 * result list, then the §4.5 footer summary line.
 *
 * The data call is unchanged. Three real bugs the live response exposed:
 *
 *   1. `results` items are WRAPPERS — {memory, score, rank, match_type,
 *      highlight} — and the whole wrapper was handed to MemoryCard as its
 *      `memory` prop. So `memory.content` was undefined and
 *      `memory.tags.map(...)` threw as soon as any result came back. The page
 *      crashed on a successful search.
 *   2. `r.__latency_ms` does not exist; the field is `latency_ms`, so the
 *      timing read "undefinedms".
 *   3. The list keyed on and linked to `m.memory_id`, which does not exist —
 *      every result linked to /memories/undefined.
 *
 * §6: the six `shimmer` blocks are replaced by skeleton rows matching the real
 * row height, so the layout does not jump when results land.
 */

const MODES = ["hybrid", "semantic", "keyword"];
const CATEGORIES = ["general", "architecture", "decision", "incident", "onboarding"];

export default function Memories() {
    const [query, setQuery] = useState("");
    const [mode, setMode] = useState("hybrid");
    const [results, setResults] = useState([]);
    const [total, setTotal] = useState(0);
    const [latency, setLatency] = useState(null);
    const [loading, setLoading] = useState(true);
    const [open, setOpen] = useState(false);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        const t = setTimeout(() => {
            api.searchMemories({ query, mode, limit: 24 }).then(r => {
                if (cancelled) return;
                setResults(Array.isArray(r?.results) ? r.results : []);
                setTotal(r?.total_found ?? 0);
                // The response field is `latency_ms`. `__latency_ms` was never
                // on it, so this rendered "undefinedms".
                setLatency(r?.latency_ms ?? r?.__latency_ms ?? null);
                setLoading(false);
            }).catch(() => {
                if (cancelled) return;
                setResults([]);
                setTotal(0);
                setLatency(null);
                setLoading(false);
            });
        }, 120);
        return () => { cancelled = true; clearTimeout(t); };
    }, [query, mode]);

    return (
        <>
            <PageHeader
                title="Memories"
                subtitle="Search your team's extracted knowledge, ranked by relevance across semantic and keyword signals."
            />

            {/* §4.2 toolbar: search, filters, right-aligned primary button. */}
            <div className="flex items-center gap-2 mb-5">
                <div className="relative flex-1 min-w-0 flex items-center">
                    <Search
                        className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-content-muted pointer-events-none"
                        aria-hidden="true"
                    />
                    <input
                        data-testid="memories-search-input"
                        type="text"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Search your team's knowledge…"
                        aria-label="Search memories"
                        className="w-full h-9 pl-10 pr-9 rounded-md bg-surface border border-hairline
                                   text-body text-content placeholder:text-content-muted
                                   focus:border-hairline-hover outline-none sm-focusable transition-colors"
                    />
                    {query && (
                        <button
                            data-testid="memories-search-clear"
                            onClick={() => setQuery("")}
                            aria-label="Clear search"
                            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-content-muted
                                       hover:text-content sm-focusable rounded"
                        >
                            <X className="w-3.5 h-3.5" />
                        </button>
                    )}
                </div>

                {/* §2: the active mode is the one blue affordance here — an
                    active tab/pill is on the accent's permitted list. */}
                <div
                    className="flex items-center gap-1 shrink-0"
                    role="group"
                    aria-label="Search mode"
                >
                    {MODES.map((m) => (
                        <button
                            key={m}
                            data-testid={`mode-${m}`}
                            onClick={() => setMode(m)}
                            aria-pressed={mode === m}
                            className={`h-9 px-3 rounded-md text-[11.5px] font-mono transition-colors sm-focusable ${
                                mode === m
                                    ? "bg-brand/10 text-brand"
                                    : "text-content-secondary hover:text-content hover:bg-white/[0.03]"
                            }`}
                        >
                            {m}
                        </button>
                    ))}
                </div>

                <Button
                    data-testid="ingest-open-btn"
                    onClick={() => setOpen(true)}
                    className="h-9 shrink-0 bg-brand-fill hover:bg-brand-fill-hover text-white text-body font-medium"
                >
                    <Plus className="w-4 h-4" /> Ingest
                </Button>
            </div>

            {loading ? (
                <div
                    className="space-y-3"
                    role="status"
                    aria-busy="true"
                    aria-label="Searching"
                >
                    {Array.from({ length: 6 }).map((_, i) => (
                        <div key={i} className="sm-card p-5 space-y-3">
                            <Skeleton className="h-[1em] w-full" />
                            <Skeleton className="h-[1em] w-4/5" />
                            <div className="flex justify-between pt-3 border-t border-hairline">
                                <Skeleton className="h-4 w-32" />
                                <Skeleton className="h-4 w-24" />
                            </div>
                        </div>
                    ))}
                    <span className="sr-only">Searching…</span>
                </div>
            ) : results.length === 0 ? (
                /* §5 template, via the shared component so the copy formula and
                   vertical rhythm match every other empty state. */
                <div className="sm-card">
                    <EmptyState
                        testId="memories-empty"
                        icon={Search}
                        headline={query ? "No matches yet" : "No memories yet"}
                        description={
                            query
                                ? "Nothing matched that query. Try different wording, or switch the search mode to broaden the match."
                                : "Ingest a document, meeting note or decision record and its extracted memories will appear here."
                        }
                        action={
                            <Button
                                onClick={() => setOpen(true)}
                                className="h-9 bg-brand-fill hover:bg-brand-fill-hover text-white text-body font-medium"
                            >
                                <Plus className="w-4 h-4" /> Ingest a document
                            </Button>
                        }
                    />
                </div>
            ) : (
                <>
                    <div className="space-y-3">
                        {results.map((r, i) => {
                            // Unwrap. `r.memory` is the memory; the rest is
                            // search metadata about it.
                            const memory = r?.memory ?? r;
                            return (
                                <MemoryCard
                                    key={memory?.id || memory?.memory_id || i}
                                    memory={memory}
                                    score={r?.score}
                                    rank={r?.rank ?? i + 1}
                                    matchType={r?.match_type}
                                />
                            );
                        })}
                    </div>

                    {/* §4.5: "Footer summary row under the table: '1–1 of 1',
                        ... '187 ms avg' — the aggregate stats are echoed once
                        more at the bottom of the list, not just at the top." */}
                    <div
                        data-testid="results-meta"
                        className="flex items-center justify-between mt-5 pt-4 border-t border-hairline"
                    >
                        <span className="font-mono text-[11px] text-content-secondary">
                            1–{results.length} of {total}
                        </span>
                        {latency != null && (
                            <span className="font-mono text-[11px] text-content-secondary">
                                {Math.round(latency)} ms
                            </span>
                        )}
                    </div>
                </>
            )}

            <IngestPanel open={open} onOpenChange={setOpen} />
        </>
    );
}

function IngestPanel({ open, onOpenChange }) {
    const [content, setContent]   = useState("");
    const [tagsRaw, setTagsRaw]   = useState("");
    const [tags, setTags]         = useState([]);
    const [category, setCategory] = useState("general");
    const [jobId, setJobId]       = useState(null);
    const [job, setJob]           = useState(null);

    useEffect(() => {
        if (!jobId) return;
        let cancelled = false;
        const poll = async () => {
            while (!cancelled) {
                const r = await api.getJobStatus(jobId);
                if (cancelled) return;
                setJob(r);
                if (r.status === "done") return;
                await new Promise(res => setTimeout(res, 180));
            }
        };
        poll();
        return () => { cancelled = true; };
    }, [jobId]);

    const addTag = (e) => {
        if (e.key === "Enter" && tagsRaw.trim()) {
            e.preventDefault();
            setTags([...tags, tagsRaw.trim()]);
            setTagsRaw("");
        } else if (e.key === "Backspace" && !tagsRaw && tags.length) {
            setTags(tags.slice(0, -1));
        }
    };

    const submit = async () => {
        if (!content.trim()) return;
        const r = await api.createMemory({ content, tags, category });
        setJobId(r.job_id);
    };

    const reset = () => {
        setContent(""); setTagsRaw(""); setTags([]); setCategory("general"); setJobId(null); setJob(null);
    };

    return (
        <Sheet open={open} onOpenChange={(v) => { onOpenChange(v); if (!v) reset(); }}>
            <SheetContent
                side="right"
                className="sm:max-w-[480px] w-[480px] bg-surface border-l border-hairline text-content p-0 overflow-y-auto"
                data-testid="ingest-panel"
            >
                <SheetHeader className="p-6 border-b border-hairline">
                    <SheetTitle className="text-content text-title">Ingest knowledge</SheetTitle>
                    <SheetDescription className="text-content-secondary text-body">
                        Paste a document, meeting notes, or decision record. SourceMind will
                        extract, chunk, embed, and attribute it.
                    </SheetDescription>
                </SheetHeader>

                {!jobId ? (
                    <div className="p-6 space-y-5">
                        <div>
                            <label htmlFor="ingest-content" className="sm-micro-label block mb-2">Content</label>
                            <Textarea
                                id="ingest-content"
                                data-testid="ingest-content"
                                value={content}
                                onChange={(e) => setContent(e.target.value)}
                                placeholder="Paste document content, meeting notes, decision records…"
                                rows={10}
                                className="bg-surface-page border-hairline text-content placeholder:text-content-muted resize-none font-mono text-[12.5px]"
                            />
                        </div>

                        <div>
                            <label htmlFor="ingest-tags" className="sm-micro-label block mb-2">Tags</label>
                            <div className="min-h-[44px] flex flex-wrap gap-1.5 p-2 rounded-md bg-surface-page border border-hairline">
                                {tags.map((t, i) => (
                                    <span key={i} className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md border border-hairline text-content-secondary text-[11px] font-mono">
                                        {t}
                                        <button onClick={() => setTags(tags.filter((_, j) => j !== i))} aria-label={`Remove tag ${t}`}>
                                            <X className="w-3 h-3" />
                                        </button>
                                    </span>
                                ))}
                                <input
                                    id="ingest-tags"
                                    data-testid="ingest-tags"
                                    value={tagsRaw}
                                    onChange={(e) => setTagsRaw(e.target.value)}
                                    onKeyDown={addTag}
                                    placeholder={tags.length ? "" : "type and press enter"}
                                    className="flex-1 min-w-[120px] bg-transparent outline-none text-[12.5px] text-content font-mono placeholder:text-content-muted"
                                />
                            </div>
                        </div>

                        <div>
                            <label className="sm-micro-label block mb-2">Category</label>
                            <Select value={category} onValueChange={setCategory}>
                                <SelectTrigger data-testid="ingest-category" className="bg-surface-page border-hairline text-content">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent className="bg-surface border-hairline text-content">
                                    {CATEGORIES.map((c) => (
                                        <SelectItem key={c} value={c} className="text-content">{c}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        <Button
                            data-testid="ingest-submit"
                            onClick={submit}
                            disabled={!content.trim()}
                            className="w-full h-10 bg-brand-fill hover:bg-brand-fill-hover text-white disabled:opacity-40"
                        >
                            Ingest &amp; process
                        </Button>
                    </div>
                ) : (
                    <div className="p-6 space-y-4">
                        {/* §6 forbids spinners as the loading indicator. This is
                            a determinate multi-stage pipeline with its own
                            tracker, so progress is shown by stage rather than
                            by a spinning icon. */}
                        <div className="flex items-center gap-2 text-body text-content-secondary">
                            {job?.status === "done" ? (
                                <span className="text-success">Pipeline complete · {job.elapsed_ms}ms total</span>
                            ) : (
                                <>
                                    Processing · stage
                                    <span className="font-mono text-brand">{job?.stage ?? "queued"}</span>
                                </>
                            )}
                        </div>
                        <PipelineTracker stages={job?.stages ?? []} currentStage={job?.stage} />
                        {job?.status === "done" && (
                            <Button onClick={reset} variant="outline" className="w-full mt-4 bg-white/[0.03] border-hairline text-content">
                                Ingest another
                            </Button>
                        )}
                    </div>
                )}
            </SheetContent>
        </Sheet>
    );
}
