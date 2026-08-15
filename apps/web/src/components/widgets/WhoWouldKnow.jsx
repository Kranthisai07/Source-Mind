import React, { useState, useRef, useEffect, useCallback } from "react";
import { Search, Users, Zap, ArrowRight, X } from "lucide-react";
import { initials, stripMarkdown } from "../../lib/format";
import api from "../../lib/api";

const PALETTE = ["#4F7EFF", "#A78BFA", "#34D399", "#F59E0B", "#EF4444", "#60A5FA", "#F472B6", "#2DD4BF"];
function avatarColor(login = "", idx = 0) {
    let h = 0;
    for (let i = 0; i < login.length; i++) h = (h * 31 + login.charCodeAt(i)) | 0;
    return PALETTE[Math.abs(h) % PALETTE.length] || PALETTE[idx % PALETTE.length];
}

const SUGGESTIONS = [
    "database migrations",
    "auth middleware",
    "incident response",
    "onboarding setup",
    "rate limiting",
    "RLS policies",
    "deployment pipeline",
    "observability",
];

function ConfidenceBar({ value }) {
    const pct = Math.round((value || 0) * 100);
    const color = pct >= 80 ? "#34D399" : pct >= 50 ? "#4F7EFF" : "#F59E0B";
    return (
        <div className="flex items-center gap-2 mt-1">
            <div className="flex-1 h-1 rounded-full bg-white/[0.06] overflow-hidden">
                <div
                    className="h-full rounded-full transition-all duration-700"
                    style={{ width: `${pct}%`, background: color }}
                />
            </div>
            <span className="text-[10px] font-mono tabular-nums" style={{ color }}>
                {pct}%
            </span>
        </div>
    );
}

export default function WhoWouldKnow({ workspaceId = "ws_acme_platform" }) {
    const [query, setQuery]     = useState("");
    const [results, setResults] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError]     = useState(null);
    const debounceRef            = useRef(null);
    const inputRef               = useRef(null);

    const runQuery = useCallback(async (q) => {
        if (!q.trim() || q.trim().length < 2) {
            setResults(null);
            return;
        }
        setLoading(true);
        setError(null);
        try {
            const data = await api.whoWouldKnow(workspaceId, { q: q.trim() });
            setResults(data);
        } catch (err) {
            setError("Search failed. Check your connection.");
        } finally {
            setLoading(false);
        }
    }, [workspaceId]);

    const handleChange = (e) => {
        const val = e.target.value;
        setQuery(val);
        clearTimeout(debounceRef.current);
        if (!val.trim()) { setResults(null); return; }
        debounceRef.current = setTimeout(() => runQuery(val), 340);
    };

    const handleSuggestion = (s) => {
        setQuery(s);
        runQuery(s);
        inputRef.current?.focus();
    };

    const clear = () => {
        setQuery("");
        setResults(null);
        inputRef.current?.focus();
    };

    // Keyboard shortcut: / focuses the input
    useEffect(() => {
        const handler = (e) => {
            if (e.key === "/" && document.activeElement?.tagName !== "INPUT" && document.activeElement?.tagName !== "TEXTAREA") {
                e.preventDefault();
                inputRef.current?.focus();
            }
        };
        window.addEventListener("keydown", handler);
        return () => window.removeEventListener("keydown", handler);
    }, []);

    const hasResults = results && results.experts && results.experts.length > 0;
    const noResults  = results && (!results.experts || results.experts.length === 0);

    return (
        <div className="sm-card p-5 space-y-4 fade-in-up">
            {/* Header */}
            <div className="flex items-center gap-2.5">
                <div
                    className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                    style={{ background: "#4F7EFF18", color: "#4F7EFF" }}
                >
                    <Users className="w-4 h-4" strokeWidth={2} />
                </div>
                <div>
                    <h2 className="text-[13px] font-semibold text-sm-text leading-none">
                        Who would know?
                    </h2>
                    <p className="text-[11px] text-sm-text-secondary mt-0.5">
                        Find the right person to ask — instantly
                    </p>
                </div>
                <div className="ml-auto">
                    <span className="text-[10px] bg-white/[0.04] border border-sm-border rounded px-1.5 py-0.5 text-sm-text-secondary font-mono">
                        /
                    </span>
                </div>
            </div>

            {/* Search input */}
            <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-sm-text-secondary pointer-events-none" />
                <input
                    ref={inputRef}
                    value={query}
                    onChange={handleChange}
                    onKeyDown={(e) => e.key === "Enter" && runQuery(query)}
                    placeholder="e.g. database migrations, auth middleware…"
                    className="w-full pl-8 pr-8 py-2.5 rounded-lg bg-white/[0.04] border border-sm-border text-[12.5px] text-sm-text placeholder:text-sm-text-secondary focus:outline-none focus:border-[#4F7EFF]/50 focus:bg-white/[0.06] transition-all"
                />
                {query && (
                    <button
                        onClick={clear}
                        className="absolute right-2.5 top-1/2 -translate-y-1/2 text-sm-text-secondary hover:text-sm-text transition-colors"
                    >
                        <X className="w-3.5 h-3.5" />
                    </button>
                )}
            </div>

            {/* Suggestion chips — show when input is empty */}
            {!query && !results && (
                <div className="flex flex-wrap gap-1.5">
                    {SUGGESTIONS.map(s => (
                        <button
                            key={s}
                            onClick={() => handleSuggestion(s)}
                            className="text-[11px] px-2.5 py-1 rounded-full bg-white/[0.04] border border-sm-border text-sm-text-secondary hover:text-sm-text hover:border-[#4F7EFF]/40 hover:bg-[#4F7EFF]/[0.08] transition-all"
                        >
                            {s}
                        </button>
                    ))}
                </div>
            )}

            {/* Loading skeleton */}
            {loading && (
                <div className="space-y-2.5">
                    {[0, 1, 2].map(i => (
                        <div key={i} className="flex items-center gap-3 p-3 rounded-lg bg-white/[0.02] border border-sm-border animate-pulse">
                            <div className="w-8 h-8 rounded-full bg-white/[0.06] flex-shrink-0" />
                            <div className="flex-1 space-y-1.5">
                                <div className="h-2.5 w-24 rounded bg-white/[0.06]" />
                                <div className="h-2 w-full rounded bg-white/[0.04]" />
                            </div>
                        </div>
                    ))}
                </div>
            )}

            {/* Error */}
            {error && !loading && (
                <div className="text-[12px] text-red-400 p-3 rounded-lg bg-red-500/[0.06] border border-red-500/20">
                    {error}
                </div>
            )}

            {/* No results */}
            {noResults && !loading && (
                <div className="text-center py-4 text-sm-text-secondary text-[12px]">
                    No experts found for <span className="text-sm-text font-medium">"{results.query}"</span>
                    <div className="text-[11px] mt-1">Try a broader term or a different topic.</div>
                </div>
            )}

            {/* Results */}
            {hasResults && !loading && (
                <div className="space-y-2">
                    {results.experts.map((expert, idx) => {
                        const color = expert.avatarColor || avatarColor(expert.login, idx);
                        const preview = stripMarkdown(expert.top_memory?.preview || "");
                        return (
                            <div
                                key={expert.user_id || idx}
                                className="flex items-start gap-3 p-3 rounded-lg bg-white/[0.02] border border-sm-border hover:bg-white/[0.04] hover:border-white/[0.12] transition-all cursor-pointer group"
                            >
                                {/* Avatar */}
                                <div
                                    className="w-8 h-8 rounded-full flex items-center justify-center text-[11px] font-bold flex-shrink-0 mt-0.5"
                                    style={{ background: `${color}22`, color }}
                                >
                                    {initials(expert.name || expert.login)}
                                </div>

                                {/* Info */}
                                <div className="flex-1 min-w-0">
                                    <div className="flex items-center gap-2 justify-between">
                                        <span className="text-[12.5px] font-semibold text-sm-text truncate">
                                            {expert.name || expert.login}
                                        </span>
                                        {idx === 0 && (
                                            <span
                                                className="text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded-full flex-shrink-0 flex items-center gap-0.5"
                                                style={{ background: "#4F7EFF18", color: "#4F7EFF" }}
                                            >
                                                <Zap className="w-2.5 h-2.5" /> Best match
                                            </span>
                                        )}
                                    </div>

                                    <ConfidenceBar value={expert.confidence} />

                                    <div className="mt-1.5 text-[11px] text-sm-text-secondary leading-snug line-clamp-2">
                                        {preview || `${expert.memory_count} memor${expert.memory_count === 1 ? "y" : "ies"} on this topic`}
                                    </div>

                                    <div className="mt-1.5 flex items-center gap-3 text-[10px] text-sm-text-secondary">
                                        <span>{expert.memory_count} memor{expert.memory_count === 1 ? "y" : "ies"}</span>
                                        {expert.top_memory?.category && (
                                            <span
                                                className="px-1.5 py-0.5 rounded text-[9.5px]"
                                                style={{ background: `${color}15`, color }}
                                            >
                                                {expert.top_memory.category}
                                            </span>
                                        )}
                                        <span className="ml-auto opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-0.5 text-[#4F7EFF]">
                                            Ask <ArrowRight className="w-2.5 h-2.5" />
                                        </span>
                                    </div>
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
