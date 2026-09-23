import React from "react";

/**
 * §6: "Every data view observed transitions through a skeleton phase ...
 * pulsing flat-gray rectangles matching the exact column widths and row
 * heights of the eventual real content. No spinners or progress bars were seen
 * as the primary loading indicator anywhere in the console — skeletons are the
 * house style."
 *
 * "Matching the exact column widths and row heights" is the load-bearing part
 * and is also what the skill's two high-severity findings ask for: Loading
 * Indicators wants an accessible busy status, and Content Jumping wants space
 * reserved so the swap to real content does not shift the layout. A skeleton
 * whose dimensions differ from the content it stands in for causes exactly the
 * CLS it was meant to prevent.
 *
 * `SkeletonText` uses em-based heights so a skeleton line occupies the same
 * space as the text size it replaces.
 */
export function Skeleton({ className = "", style, ...rest }) {
    return <div className={`sm-skeleton ${className}`} style={style} {...rest} />;
}

/** A run of text lines. `width` applies to the last line, which reads as ragged. */
export function SkeletonText({ lines = 1, className = "", lastLineWidth = "60%" }) {
    return (
        <div className={`space-y-2 ${className}`}>
            {Array.from({ length: lines }).map((_, i) => (
                <Skeleton
                    key={i}
                    className="h-[1em]"
                    style={i === lines - 1 && lines > 1 ? { width: lastLineWidth } : undefined}
                />
            ))}
        </div>
    );
}

/**
 * Wraps a loading region so assistive tech is told it is busy, per the skill's
 * "Loading Indicators" guideline (severity: High — "preserve layout focus and
 * accessible busy status"). Renders children once loading resolves.
 */
export function SkeletonRegion({ loading, label = "Loading", children, fallback }) {
    if (!loading) return children;
    return (
        <div role="status" aria-busy="true" aria-label={label}>
            {fallback}
            <span className="sr-only">{label}…</span>
        </div>
    );
}

export default Skeleton;
