import React from "react";
import { Link } from "react-router-dom";

/** The landing page's mark, drawn with app tokens so it needs no marketing stylesheet. */
export function Mark({ size = 24, className = "" }) {
    return (
        <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true" focusable="false" className={`shrink-0 ${className}`}>
            <path d="M4 12L16 6L28 12L16 18L4 12Z" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
            <path d="M4 19L16 25L28 19" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            <circle cx="16" cy="6" r="2.5" style={{ fill: "var(--sm-purple)" }} />
        </svg>
    );
}

/** Mark + "SourceMind." wordmark, linking to `to`. `compact` shows the mark only. */
export default function AppBrand({ to = "/dashboard", compact = false, testId = "app-brand" }) {
    return (
        <Link to={to} data-testid={testId} aria-label="SourceMind" className="inline-flex items-center gap-2.5 text-sm-text hover:opacity-80 transition-opacity">
            <Mark size={compact ? 26 : 24} />
            {!compact && (
                <span className="text-[18px] font-semibold leading-none tracking-tight">
                    SourceMind<span style={{ color: "var(--sm-purple)" }}>.</span>
                </span>
            )}
        </Link>
    );
}
