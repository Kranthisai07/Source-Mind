import React from "react";
import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";
import TopStrip from "./TopStrip";

/**
 * §1: "Fixed left sidebar (~260px) + fluid main content area", with the ~48px
 * strip sitting above the CONTENT COLUMN ONLY — not spanning the full viewport.
 * That is why TopStrip renders inside <main> rather than above this flex row.
 *
 * The collapsed/onToggle state is gone: §1 states the sidebar has no collapse
 * control, just an overflow scroll region.
 *
 * Routing is untouched — this still renders <Outlet /> exactly as before.
 */
export default function AppLayout() {
    return (
        <div className="flex min-h-screen bg-surface-page text-content">
            <Sidebar />
            <main className="flex-1 min-w-0 flex flex-col">
                <TopStrip />
                <div className="flex-1 px-8 py-8">
                    <div className="mx-auto w-full max-w-[1280px]">
                        <Outlet />
                    </div>
                </div>
            </main>
        </div>
    );
}
