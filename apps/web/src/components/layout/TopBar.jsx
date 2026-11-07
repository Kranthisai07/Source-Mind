import React from "react";
import { Link } from "react-router-dom";

import ThemeToggle from "@/components/widgets/ThemeToggle";

/** `crumbs`: [{ label, to }] shown above the title on detail pages. */
export default function TopBar({ title, subtitle, actions, crumbs }) {
    return (
        <header
            data-testid="topbar"
            className="min-h-16 py-2 shrink-0 sticky top-0 z-20 flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-8 border-b border-sm-border bg-sm-bg/80 backdrop-blur-xl"
        >
            {/* basis keeps the title block wide enough to read; when the actions do not fit beside it they wrap below */}
            <div className="min-w-0 flex-1 basis-[14rem]">
                {crumbs?.length > 0 && (
                    <nav aria-label="Breadcrumb" data-testid="breadcrumbs" className="font-mono text-[10px] uppercase tracking-[0.14em] text-sm-text-muted mb-0.5 flex items-center gap-1.5">
                        {crumbs.map((c, i) => (
                            <React.Fragment key={c.label}>
                                {i > 0 && <span aria-hidden="true">/</span>}
                                <Link to={c.to} className="hover:text-sm-text transition-colors">{c.label}</Link>
                            </React.Fragment>
                        ))}
                    </nav>
                )}
                <h1 data-testid="page-title" className="font-serif text-[22px] font-normal text-sm-text leading-tight">
                    {title}
                </h1>
                {subtitle && (
                    <p data-testid="page-subtitle" className="text-[12px] text-sm-text-secondary leading-tight mt-0.5">
                        {subtitle}
                    </p>
                )}
            </div>
            <div data-testid="topbar-actions" className="flex items-center gap-2 shrink-0">
                {actions}
                <ThemeToggle />
            </div>
        </header>
    );
}
