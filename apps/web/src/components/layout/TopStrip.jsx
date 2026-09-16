import React, { useEffect, useState } from "react";
import { ChevronDown, Check, HelpCircle, BookOpen } from "lucide-react";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "../ui/dropdown-menu";
import api from "../../lib/api";

/**
 * §1: "No top nav bar in the traditional sense — instead a slim top strip
 * (~48px) sits above the content column only (not full-width), holding:
 * workspace/org switcher (left), and Help / Docs ↗ links (right)."
 *
 * "Above the content column only" is why this renders inside <main>, beside
 * the sidebar rather than across it. The previous TopBar was a 64px full-width
 * header that also carried the page title; the title moves to PageHeader,
 * because §1 puts H1 in the content area, not in the strip.
 *
 * §4.12: the switcher is "avatar + org name + plan badge + chevron", opening to
 * "current org row with a checkmark, then + Create organization". The plan
 * badge is dropped — commercial positioning is explicitly out of scope — and
 * the workspace slug takes its place, which §3 says should be monospace
 * ("container-tag slugs ... a clear 'this is technical/copyable' signal").
 *
 * Read-only: it renders whatever api.getWorkspace() already returns. There is
 * no workspace-creation endpoint wired in the frontend, so no create action is
 * offered rather than showing a control that would not work.
 */
export default function TopStrip() {
    const [workspace, setWorkspace] = useState(null);

    useEffect(() => {
        api.getWorkspace().then(setWorkspace).catch(() => setWorkspace(null));
    }, []);

    const wsName = workspace?.name || "Workspace";
    const wsSlug = workspace?.slug || "";

    return (
        <div
            data-testid="top-strip"
            className="h-topstrip shrink-0 sticky top-0 z-20 flex items-center justify-between
                       px-8 border-b border-hairline bg-surface-page/85 backdrop-blur-xl"
        >
            <DropdownMenu>
                <DropdownMenuTrigger asChild>
                    <button
                        data-testid="workspace-switcher"
                        className="flex items-center gap-2 h-7 px-2 -ml-2 rounded-md
                                   text-content hover:bg-white/[0.04] transition-colors sm-focusable"
                    >
                        <div className="w-4 h-4 rounded flex items-center justify-center shrink-0
                                        bg-surface-hover border border-hairline
                                        text-[9px] font-semibold text-content-secondary">
                            {wsName.charAt(0).toUpperCase()}
                        </div>
                        <span className="text-body font-medium truncate max-w-[220px]">{wsName}</span>
                        {wsSlug && (
                            <span className="font-mono text-[11px] text-content-secondary truncate max-w-[160px]">
                                {wsSlug}
                            </span>
                        )}
                        <ChevronDown className="w-3.5 h-3.5 text-content-muted shrink-0" strokeWidth={2} aria-hidden="true" />
                    </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                    align="start"
                    sideOffset={6}
                    className="w-64 bg-surface border-hairline text-content"
                >
                    <DropdownMenuItem className="cursor-default text-body focus:bg-transparent">
                        <Check className="w-4 h-4 mr-2 text-brand" strokeWidth={2.5} aria-hidden="true" />
                        <span className="truncate">{wsName}</span>
                    </DropdownMenuItem>
                </DropdownMenuContent>
            </DropdownMenu>

            <div className="flex items-center gap-1 shrink-0">
                <a
                    href="https://github.com/Kranthisai07/Source-Mind#readme"
                    target="_blank"
                    rel="noreferrer"
                    className="h-7 px-2.5 rounded-md flex items-center gap-1.5 text-body
                               text-content-secondary hover:text-content hover:bg-white/[0.04]
                               transition-colors sm-focusable"
                >
                    <HelpCircle className="w-3.5 h-3.5" strokeWidth={2} aria-hidden="true" />
                    Help
                </a>
                <a
                    href="https://github.com/Kranthisai07/Source-Mind/tree/main/docs"
                    target="_blank"
                    rel="noreferrer"
                    className="h-7 px-2.5 rounded-md flex items-center gap-1.5 text-body
                               text-content-secondary hover:text-content hover:bg-white/[0.04]
                               transition-colors sm-focusable"
                >
                    <BookOpen className="w-3.5 h-3.5" strokeWidth={2} aria-hidden="true" />
                    Docs ↗
                </a>
            </div>
        </div>
    );
}
