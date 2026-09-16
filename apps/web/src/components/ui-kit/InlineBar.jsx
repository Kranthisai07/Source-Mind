import React from "react";

/**
 * §4.5: "Duration renders as *both* a number ("187") and an inline horizontal
 * progress-bar-style latency visualization next to it in the same cell — a
 * nice compact 'duration as mini-bar' pattern."
 *
 * Applied here to attribution percentages and contribution weights: the number
 * and a compact bar occupy ONE cell, rather than the number in a cell and a
 * separate large gauge elsewhere.
 *
 * The bar is decorative — the number beside it already carries the value — so
 * it is aria-hidden and the cell reads once, not twice, to a screen reader.
 * That also satisfies the skill's "don't rely on colour alone": the figure is
 * always present in text.
 *
 * §2 permits one accent hue, so magnitude is encoded by bar LENGTH and, where a
 * set needs distinguishing, by opacity steps from --c-data-1..4 — never by
 * switching hue.
 */
export default function InlineBar({
    value,
    max = 100,
    display,
    suffix = "%",
    tone = "accent",
    barColor,
    width,
    className = "",
    mono = true,
    testId,
}) {
    const safeMax = max > 0 ? max : 1;
    const pct = Math.max(0, Math.min(100, (value / safeMax) * 100));

    /* An explicit barColor wins, so a set of related bars can step through the
       --c-data-1..4 opacity ramp. Falls back to the semantic tone. */
    const resolvedBar =
        barColor
        ?? (tone === "success" ? "var(--c-status-success)"
            : tone === "warning" ? "var(--c-status-warning)"
            : tone === "danger"  ? "var(--c-status-danger)"
            : "var(--c-accent)");

    return (
        <div
            data-testid={testId}
            className={`flex items-center gap-2.5 ${className}`}
        >
            <span
                className={`text-body text-content tabular-nums ${mono ? "font-mono" : ""}`}
            >
                {display ?? value}
                {suffix}
            </span>
            {/* A fixed-width bar must not shrink; a full-width one must flex,
               or it overflows the row once the numeral takes its space. */}
            <div
                aria-hidden="true"
                className={`h-[4px] rounded-full overflow-hidden ${
                    width === "100%" ? "flex-1 min-w-0" : "shrink-0"
                }`}
                style={{
                    width: width === "100%" ? undefined : (width ?? "var(--inline-bar-width)"),
                    background: "var(--c-data-track)",
                }}
            >
                <div
                    className="h-full rounded-full transition-[width] duration-500 ease-out"
                    style={{ width: `${pct}%`, background: resolvedBar }}
                />
            </div>
        </div>
    );
}
