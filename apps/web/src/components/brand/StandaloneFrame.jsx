import React from "react";
import AppBrand from "./AppBrand";
import ThemeToggle from "../widgets/ThemeToggle";

/** Branded frame for screens outside the app shell (invitation, loading, errors). */
export default function StandaloneFrame({ children, testId, width = "max-w-md", brandTo = "/", actions }) {
    return (
        <div className="min-h-screen bg-sm-bg text-sm-text flex flex-col" data-testid={testId}>
            <header className="h-16 px-6 flex items-center justify-between border-b border-sm-border">
                <AppBrand to={brandTo} />
                <div className="flex items-center gap-2">
                    {actions}
                    <ThemeToggle />
                </div>
            </header>
            <main className="flex-1 flex items-start justify-center px-6 pt-16 pb-16">
                <div className={`w-full ${width}`}>{children}</div>
            </main>
            <footer className="px-6 py-4 border-t border-sm-border font-mono text-[10px] uppercase tracking-[0.14em] text-sm-text-muted">
                Shared knowledge. Lasting context.
            </footer>
        </div>
    );
}
