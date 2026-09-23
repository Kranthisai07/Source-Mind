import React from "react";
import { Skeleton } from "./Skeleton";

/**
 * §4.5's stat card, plus §3's hero-metric treatment:
 *
 *   "Three stat cards side by side, each with a small uppercase label"
 *   "Numbers get outsized visual weight where they're the point ... rendered
 *    much larger than their labels, in a semi-bold/mono-ish numeral style."
 *
 * So: an ~11px uppercase letter-spaced micro-label, then the figure at 34px
 * semibold in the mono face, then an optional caption. §2 forbids a second
 * accent hue, so there is no per-card accent colour — the old implementation
 * gave each of four cards its own hue (blue/purple/green/amber), which is
 * precisely the "competing with the accent" the reference rules out.
 *
 * `tone` exists only for genuinely semantic figures (an open-conflict count
 * that is non-zero is a warning; zero is a success), never for decoration.
 *
 * The skeleton is rendered at the same 34px line box as the real figure so the
 * card does not resize when data lands.
 */
export default function StatCard({
    label,
    value,
    caption,
    icon: Icon,
    tone = "neutral",
    loading = false,
    testId,
}) {
    const valueColor =
        tone === "success" ? "text-success"
        : tone === "warning" ? "text-warning"
        : tone === "danger"  ? "text-danger"
        : "text-content";

    return (
        <div data-testid={testId} className="sm-card p-6 flex flex-col gap-3">
            <div className="flex items-center justify-between gap-2">
                <span className="sm-micro-label">{label}</span>
                {Icon && (
                    <Icon
                        className="w-4 h-4 text-content-muted shrink-0"
                        strokeWidth={1.75}
                        aria-hidden="true"
                    />
                )}
            </div>

            {loading ? (
                <Skeleton className="h-[34px] w-24" />
            ) : (
                <div
                    data-testid={testId ? `${testId}-value` : undefined}
                    className={`font-mono text-hero tabular-nums ${valueColor}`}
                >
                    {value}
                </div>
            )}

            {loading ? (
                <Skeleton className="h-[1em] w-32" />
            ) : (
                caption && (
                    <div className="text-body text-content-secondary leading-snug">
                        {caption}
                    </div>
                )
            )}
        </div>
    );
}
