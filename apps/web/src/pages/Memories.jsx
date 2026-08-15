import React, { useEffect, useMemo, useState } from "react";
import { Search, Plus, X, Loader2 } from "lucide-react";
import TopBar from "../components/layout/TopBar";
import MemoryCard from "../components/widgets/MemoryCard";
import PipelineTracker from "../components/widgets/PipelineTracker";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Textarea } from "../components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "../components/ui/sheet";
import api from "../lib/api";

const MODES = ["hybrid", "semantic", "keyword"];
const CATEGORIES = ["general", "architecture", "decision", "incident", "onboarding"];

export default function Memories() {
    const [query, setQuery] = useState("");
    const [mode, setMode] = useState("hybrid");
    const [results, setResults] = useState([]);
    const [total, setTotal] = useState(0);
    const [latency, setLatency] = useState(0);
    const [loading, setLoading] = useState(true);
    const [open, setOpen] = useState(false);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        const t = setTimeout(() => {
            api.searchMemories({ query, mode, limit: 24 }).then(r => {
                if (cancelled) return;
                setResults(r.results);
                setTotal(r.total_found);
                setLatency(r.__latency_ms);
                setLoading(false);
            });
        }, 120);
        return () => { cancelled = true; clearTimeout(t); };
    }, [query, mode]);

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
                {/* Search */}
                <div className="sm-card p-1.5 flex items-center gap-2">
                    <div className="relative flex-1 flex items-center">
                        <Search className="w-4 h-4 absolute left-4 top-1/2 -translate-y-1/2 text-sm-text-muted pointer-events-none" />
                        <input
                            data-testid="memories-search-input"
                            type="text"
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            placeholder="Search your team's knowledge..."
                            className="w-full h-11 pl-11 pr-3 bg-transparent text-[14px] text-sm-text placeholder:text-sm-text-muted outline-none"
                        />
                        {query && (
                            <button
                                data-testid="memories-search-clear"
                                onClick={() => setQuery("")}
                                className="absolute right-3 top-1/2 -translate-y-1/2 text-sm-text-muted hover:text-sm-text"
                            >
                                <X className="w-4 h-4" />
                            </button>
                        )}
                    </div>
                    <div className="flex items-center gap-1 pr-2">
                        {MODES.map((m) => (
                            <button
                                key={m}
                                data-testid={`mode-${m}`}
                                onClick={() => setMode(m)}
                                className={`h-8 px-3 rounded-md text-[11.5px] font-mono tracking-wide transition-colors ${
                                    mode === m
                                        ? "bg-sm-blue/15 text-sm-blue border border-sm-blue/30"
                                        : "text-sm-text-secondary hover:text-sm-text border border-transparent hover:bg-white/[0.03]"
                                }`}
                            >
                                {m}
                            </button>
                        ))}
                    </div>
                </div>

                <div className="flex items-center justify-between">
                    <p data-testid="results-meta" className="font-mono text-[11.5px] text-sm-text-secondary">
                        {loading
                            ? "searching…"
                            : <>{total} result{total === 1 ? "" : "s"} · <span className="text-sm-text">{latency}ms</span></>
                        }
                    </p>
                </div>

                {/* Results */}
                {loading ? (
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                        {Array.from({ length: 6 }).map((_, i) => (
                            <div key={i} className="sm-card p-5 h-[200px] shimmer rounded-xl" />
                        ))}
                    </div>
                ) : results.length === 0 ? (
                    <EmptyState onIngest={() => setOpen(true)} />
                ) : (
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                        {results.map((m) => (
                            <MemoryCard key={m.memory_id} memory={m} />
                        ))}
                    </div>
                )}
            </div>

            <IngestPanel open={open} onOpenChange={setOpen} />
        </>
    );
}

function EmptyState({ onIngest }) {
    return (
        <div className="sm-card py-24 flex flex-col items-center text-center" data-testid="memories-empty">
            <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-sm-blue/20 to-sm-purple/20 border border-sm-border flex items-center justify-center mb-5">
                <Search className="w-7 h-7 text-sm-blue" strokeWidth={1.8} />
            </div>
            <h3 className="text-[16px] font-semibold text-sm-text mb-2">No matches yet</h3>
            <p className="text-[13px] text-sm-text-secondary max-w-sm mb-6">
                Try a different query, broaden your search mode, or ingest a document to seed the knowledge base.
            </p>
            <Button onClick={onIngest} className="bg-sm-blue hover:bg-sm-blue/90 text-white h-9">
                <Plus className="w-4 h-4" /> Ingest first document
            </Button>
        </div>
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
        const r = await api.createMemory({ content, tags, category, workspace_id: "ws_acme_platform" });
        setJobId(r.job_id);
    };

    const reset = () => {
        setContent(""); setTagsRaw(""); setTags([]); setCategory("general"); setJobId(null); setJob(null);
    };

    return (
        <Sheet open={open} onOpenChange={(v) => { onOpenChange(v); if (!v) reset(); }}>
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
                            <label className="block text-[12px] text-sm-text-secondary mb-2 font-medium">Content</label>
                            <Textarea
                                data-testid="ingest-content"
                                value={content}
                                onChange={(e) => setContent(e.target.value)}
                                placeholder="Paste document content, meeting notes, decision records..."
                                rows={10}
                                className="bg-sm-bg/60 border-sm-border text-sm-text placeholder:text-sm-text-muted resize-none font-mono text-[12.5px]"
                            />
                        </div>

                        <div>
                            <label className="block text-[12px] text-sm-text-secondary mb-2 font-medium">Tags</label>
                            <div className="min-h-[44px] flex flex-wrap gap-1.5 p-2 rounded-lg bg-sm-bg/60 border border-sm-border">
                                {tags.map((t, i) => (
                                    <span key={i} className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-sm-blue/15 border border-sm-blue/30 text-sm-blue text-[11px] font-mono">
                                        {t}
                                        <button onClick={() => setTags(tags.filter((_, j) => j !== i))}>
                                            <X className="w-3 h-3" />
                                        </button>
                                    </span>
                                ))}
                                <input
                                    data-testid="ingest-tags"
                                    value={tagsRaw}
                                    onChange={(e) => setTagsRaw(e.target.value)}
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
                                    {CATEGORIES.map((c) => (
                                        <SelectItem key={c} value={c} className="text-sm-text focus:bg-white/5 focus:text-sm-text">{c}</SelectItem>
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
                            Ingest & Process
                        </Button>
                    </div>
                ) : (
                    <div className="p-6 space-y-4">
                        <div className="flex items-center gap-2 text-[12.5px] text-sm-text-secondary">
                            {job?.status === "done" ? (
                                <span className="text-sm-green flex items-center gap-1.5">● Pipeline complete · {job.elapsed_ms}ms total</span>
                            ) : (
                                <>
                                    <Loader2 className="w-3.5 h-3.5 animate-spin text-sm-blue" />
                                    Processing · stage: <span className="font-mono text-sm-blue">{job?.stage}</span>
                                </>
                            )}
                        </div>
                        <PipelineTracker stages={job?.stages ?? []} currentStage={job?.stage} />
                        {job?.status === "done" && (
                            <Button onClick={reset} variant="outline" className="w-full mt-4 bg-white/[0.03] border-sm-border text-sm-text hover:bg-white/[0.06]">
                                Ingest another
                            </Button>
                        )}
                    </div>
                )}
            </SheetContent>
        </Sheet>
    );
}
