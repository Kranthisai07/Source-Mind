import React from "react";

// Thin horizontal segmented bar showing % contribution per author.
// Each segment is colored by the author's avatar color.
export default function AttributionBar({ attribution = [], height = 4 }) {
    const rows = Array.isArray(attribution) ? attribution : [];
    const total = rows.reduce((acc, a) => acc + (a.score || 0), 0) || 1;
    return (
        <div className="w-full" data-testid="attribution-bar">
            <div
                className="flex w-full rounded-full overflow-hidden bg-sm-border/60"
                style={{ height }}
            >
                {rows.map((a, i) => (
                    <div
                        key={i}
                        className="h-full transition-all"
                        style={{
                            width: `${(a.score / total) * 100}%`,
                            background: a.color,
                            borderRight: i < rows.length - 1 ? "1px solid var(--sm-bg)" : "none",
                        }}
                        title={`${a.author} · ${Math.round((a.score / total) * 100)}%`}
                    />
                ))}
            </div>
        </div>
    );
}
