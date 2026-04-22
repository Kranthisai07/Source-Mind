import React from "react";

// Accepts the Memory contract shape. Renders a compact card.
import ContributorAvatar from "./ContributorAvatar";
import AttributionBar from "./AttributionBar";
import { relativeTime, stripMarkdown } from "../../lib/format";
import { CONTRIBUTORS } from "../../lib/mockData";
import { useNavigate } from "react-router-dom";

const TAG_COLORS = [
    { bg: "#4F7EFF18", text: "#93B3FF", border: "#4F7EFF33" },
    { bg: "#A78BFA18", text: "#C4B5FD", border: "#A78BFA33" },
    { bg: "#34D39918", text: "#6EE7B7", border: "#34D39933" },
    { bg: "#F59E0B18", text: "#FCD34D", border: "#F59E0B33" },
    { bg: "#EF444418", text: "#FCA5A5", border: "#EF444433" },
];
const hashTag = (s) => {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return TAG_COLORS[Math.abs(h) % TAG_COLORS.length];
};

export default function MemoryCard({ memory }) {
    const navigate = useNavigate();
    const primary = memory.attribution?.[0];
    const contributor = CONTRIBUTORS.find(c => c.login === primary?.author) || { name: primary?.name, login: primary?.author, avatarColor: primary?.color };
    return (
        <article
            data-testid={`memory-card-${memory.memory_id}`}
            onClick={() => navigate(`/memories/${memory.memory_id}`)}
            className="sm-card p-5 flex flex-col gap-4 cursor-pointer group fade-in-up"
        >
            <p className="text-[13.5px] text-sm-text leading-relaxed line-clamp-2">
                {stripMarkdown(memory.content)}
            </p>

            <div className="flex items-center gap-1.5 flex-wrap">
                {memory.tags.map((t) => {
                    const c = hashTag(t);
                    return (
                        <span
                            key={t}
                            className="text-[10.5px] font-mono px-2 py-0.5 rounded-md border"
                            style={{ background: c.bg, color: c.text, borderColor: c.border }}
                        >
                            {t}
                        </span>
                    );
                })}
                <span className="text-[10.5px] font-mono px-2 py-0.5 rounded-md border border-sm-border text-sm-text-secondary ml-auto">
                    {memory.category}
                </span>
            </div>

            <AttributionBar attribution={memory.attribution} />

            <div className="flex items-center justify-between pt-1">
                <div className="flex items-center gap-2 min-w-0">
                    <ContributorAvatar contributor={contributor} size={22} />
                    <div className="min-w-0">
                        <div className="text-[12px] font-medium text-sm-text truncate">{contributor.name}</div>
                        <div className="font-mono text-[10.5px] text-sm-text-secondary truncate">@{contributor.login}</div>
                    </div>
                </div>
                <div className="flex items-center gap-3">
                    <span className="font-mono text-[10.5px] text-sm-text-secondary">{relativeTime(memory.created_at)}</span>
                    <span className="text-[11px] text-sm-blue opacity-0 group-hover:opacity-100 transition-opacity">View →</span>
                </div>
            </div>
        </article>
    );
}
