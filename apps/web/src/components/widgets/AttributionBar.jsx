import React from "react";

// Thin horizontal segmented bar showing % contribution per author.
// Each segment is colored by the author's avatar color.
export default function AttributionBar({ attribution, height = 4 }) {
    // A default parameter only fills in for `undefined`, not `null` — and
    // search results carry `attribution: null`, so `null.reduce(...)` threw
    // here. Normalise explicitly instead of relying on the default.
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
                            /* §2 permits one accent hue: segments step
                               through the blue ramp by opacity rather than
                               each contributor owning a colour. */
                            background: `var(--c-data-${Math.min(i + 1, 4)})`,
                            borderRight: i < rows.length - 1 ? "1px solid #12121A" : "none",
                        }}
                        title={`${a.author} · ${Math.round((a.score / total) * 100)}%`}
                    />
                ))}
            </div>
        </div>
    );
}
