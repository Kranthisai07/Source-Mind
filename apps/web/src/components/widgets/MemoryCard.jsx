import React from "react";
import { useNavigate } from "react-router-dom";
import ContributorAvatar from "./ContributorAvatar";
import AttributionBar from "./AttributionBar";
import { relativeTime, stripMarkdown } from "../../lib/format";

const TAG_COLORS = [
    { bg: "#4F7EFF18", text: "#93B3FF", border: "#4F7EFF33" },
    { bg: "#A78BFA18", text: "#C4B5FD", border: "#A78BFA33" },
    { bg: "#34D39918", text: "#6EE7B7", border: "#34D39933" },
    { bg: "#F59E0B18", text: "#FCD34D", border: "#F59E0B33" },
    { bg: "#EF444418", text: "#FCA5A5", border: "#EF444433" },
];

const hashTag = (value) => {
    let hash = 0;
    for (let index = 0; index < value.length; index += 1) hash = (hash * 31 + value.charCodeAt(index)) | 0;
    return TAG_COLORS[Math.abs(hash) % TAG_COLORS.length];
};

export default function MemoryCard({ memory, score, rank, matchType }) {
    const navigate = useNavigate();
    if (!memory) return null;

    const id = memory.id || memory.memory_id;
    const tags = Array.isArray(memory.tags) ? memory.tags : [];
    const attribution = Array.isArray(memory.attribution) ? memory.attribution : [];
    const primary = attribution.find((item) => item.is_primary) || attribution[0] || null;
    const contributor = primary ? {
        name: primary.name || primary.author,
        login: primary.author,
        avatarColor: primary.color || "#4F7EFF",
    } : null;

    return (
        <article
            data-testid={`memory-card-${id}`}
            onClick={() => id && navigate(`/memories/${id}`)}
            onKeyDown={(event) => {
                if ((event.key === "Enter" || event.key === " ") && id) {
                    event.preventDefault();
                    navigate(`/memories/${id}`);
                }
            }}
            role="link"
            tabIndex={0}
            className="sm-card p-5 flex flex-col gap-4 cursor-pointer group fade-in-up"
        >
            <div className="flex items-start gap-3">
                {typeof rank === "number" && <span className="font-mono text-[10.5px] text-sm-text-muted pt-0.5">{rank}</span>}
                <p className="text-[13.5px] text-sm-text leading-relaxed line-clamp-2 flex-1">
                    {stripMarkdown(memory.content || "")}
                </p>
            </div>

            <div className="flex items-center gap-1.5 flex-wrap">
                {tags.map((tag) => {
                    const color = hashTag(tag);
                    return (
                        <span key={tag} className="text-[10.5px] font-mono px-2 py-0.5 rounded-md border" style={{ background: color.bg, color: color.text, borderColor: color.border }}>
                            {tag}
                        </span>
                    );
                })}
                {memory.category && <span className="text-[10.5px] font-mono px-2 py-0.5 rounded-md border border-sm-border text-sm-text-secondary ml-auto">{memory.category}</span>}
            </div>

            {attribution.length > 0 && <AttributionBar attribution={attribution} />}

            <div className="flex items-center justify-between pt-1 gap-4">
                <div className="flex items-center gap-2 min-w-0">
                    {contributor ? <>
                        <ContributorAvatar contributor={contributor} size={22} />
                        <div className="min-w-0">
                            <div className="text-[12px] font-medium text-sm-text truncate">{contributor.name}</div>
                            <div data-testid="memory-attribution-handle" className="font-mono text-[10.5px] text-sm-text-secondary truncate">@{contributor.login}</div>
                        </div>
                    </> : (
                        <div data-testid="memory-attribution-unattributed" className="text-[12px] font-medium text-sm-text-secondary">Unattributed</div>
                    )}
                </div>
                <div className="flex items-center gap-3 shrink-0">
                    {matchType && <span className="font-mono text-[10px] text-sm-text-secondary">{matchType}</span>}
                    {typeof score === "number" && <span data-testid="result-score" className="font-mono text-[10.5px] text-sm-blue">{score.toFixed(3)}</span>}
                    <span className="font-mono text-[10.5px] text-sm-text-secondary">{memory.created_at ? relativeTime(memory.created_at) : "—"}</span>
                    <span className="text-[11px] text-sm-blue opacity-0 group-hover:opacity-100 transition-opacity">View →</span>
                </div>
            </div>
        </article>
    );
}
