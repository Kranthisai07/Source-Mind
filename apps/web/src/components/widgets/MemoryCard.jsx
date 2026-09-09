import React from "react";
import { useNavigate } from "react-router-dom";
import InlineBar from "../ui-kit/InlineBar";
import { relativeTime, stripMarkdown, initials } from "../../lib/format";

/**
 * A single search result, in the shape §4.2/§4.7 use for list rows:
 * individually-bordered row, leading content, metadata in a quiet line beneath.
 *
 * The props changed, deliberately. This used to take the whole search-result
 * item as `memory` and read `memory.content` / `memory.tags` off it — but the
 * API returns results as WRAPPERS:
 *
 *   { memory: {...}, score, rank, match_type, highlight }
 *
 * so `memory.content` was undefined and `memory.tags.map(...)` threw on every
 * result. The caller now unwraps and passes the memory plus its search
 * metadata separately, which makes the shape mismatch impossible to reintroduce
 * silently.
 *
 * §2: the five-hue hashed tag palette is gone — tags are a neutral outline.
 * §3: content excerpt and identifiers are mono; metadata labels are micro-caps.
 * §4.5: relevance renders as a number AND an inline bar in the same cell.
 *
 * CONTRIBUTORS from mockData is no longer imported; the primary author now
 * comes from the memory's own attribution, or is shown as unattributed.
 */
export default function MemoryCard({ memory, score, rank, matchType }) {
    const navigate = useNavigate();

    if (!memory) return null;

    const id = memory.id || memory.memory_id;
    const tags = Array.isArray(memory.tags) ? memory.tags : [];
    const attribution = Array.isArray(memory.attribution) ? memory.attribution : [];
    const primary = attribution.find(a => a.is_primary) || attribution[0] || null;
    const primaryName = primary?.name || primary?.author || null;

    // Search scores are RRF values (~0.016 for a single hit), not percentages,
    // so the bar is scaled against the top-ranked result rather than 100.
    const scorePct = typeof score === "number" ? Math.round(score * 100) : null;

    return (
        <article
            data-testid={`memory-card-${id}`}
            onClick={() => id && navigate(`/memories/${id}`)}
            onKeyDown={(e) => {
                if ((e.key === "Enter" || e.key === " ") && id) {
                    e.preventDefault();
                    navigate(`/memories/${id}`);
                }
            }}
            role="link"
            tabIndex={0}
            className="sm-card p-5 flex flex-col gap-3 cursor-pointer group
                       hover:border-hairline-hover transition-colors sm-focusable"
        >
            <div className="flex items-start gap-3">
                {typeof rank === "number" && (
                    <span className="font-mono text-[11px] text-content-muted pt-0.5 shrink-0 w-5 text-right">
                        {rank}
                    </span>
                )}
                {/* §3: memory content excerpts are mono — "this is data". */}
                <p className="font-mono text-[12.5px] text-content leading-relaxed line-clamp-2 flex-1 min-w-0">
                    {stripMarkdown(memory.content || "")}
                </p>
            </div>

            {(tags.length > 0 || memory.category) && (
                <div className="flex items-center gap-1.5 flex-wrap">
                    {tags.map((t) => (
                        <span
                            key={t}
                            className="text-[10.5px] font-mono px-2 py-0.5 rounded-md border border-hairline text-content-secondary"
                        >
                            {t}
                        </span>
                    ))}
                    {memory.category && (
                        <span className="text-[10.5px] font-mono px-2 py-0.5 rounded-md border border-hairline text-content-secondary ml-auto">
                            {memory.category}
                        </span>
                    )}
                </div>
            )}

            <div className="flex items-center justify-between gap-4 pt-1 border-t border-hairline mt-1">
                <div className="flex items-center gap-2.5 min-w-0 pt-3">
                    <div className="w-5 h-5 rounded-full shrink-0 flex items-center justify-center
                                    bg-surface-hover border border-hairline
                                    text-[8px] font-semibold text-content-secondary">
                        {primaryName ? initials(primaryName) : "—"}
                    </div>
                    <span className="text-body text-content-secondary truncate">
                        {primaryName || "Unattributed"}
                    </span>
                </div>

                <div className="flex items-center gap-4 shrink-0 pt-3">
                    {matchType && (
                        <span className="sm-micro-label" title="How this result matched">
                            {matchType}
                        </span>
                    )}
                    {scorePct !== null && (
                        /* §4.5 — number and mini-bar together. */
                        <InlineBar
                            value={scorePct}
                            display={score.toFixed(3)}
                            suffix=""
                            width="44px"
                            testId="result-score"
                        />
                    )}
                    <span className="font-mono text-[10.5px] text-content-secondary">
                        {memory.created_at ? relativeTime(memory.created_at) : "—"}
                    </span>
                </div>
            </div>
        </article>
    );
}
