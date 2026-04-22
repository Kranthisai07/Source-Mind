import React from "react";

export default function TopBar({ title, subtitle, actions }) {
    return (
        <header
            data-testid="topbar"
            className="h-16 shrink-0 sticky top-0 z-20 flex items-center justify-between px-8 border-b border-sm-border bg-sm-bg/80 backdrop-blur-xl"
        >
            <div className="min-w-0">
                <h1 data-testid="page-title" className="text-[18px] font-semibold text-sm-text leading-tight tracking-tight">
                    {title}
                </h1>
                {subtitle && (
                    <p data-testid="page-subtitle" className="text-[12px] text-sm-text-secondary leading-tight mt-0.5">
                        {subtitle}
                    </p>
                )}
            </div>
            {actions && (
                <div data-testid="topbar-actions" className="flex items-center gap-2 shrink-0">
                    {actions}
                </div>
            )}
        </header>
    );
}
