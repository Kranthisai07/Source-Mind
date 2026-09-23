import React, { useEffect, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useClerk, useUser } from "@clerk/clerk-react";
import {
    Home, Brain, BarChart3, AlertTriangle, ArrowRightLeft,
    Plug, Settings as SettingsIcon, LogOut, Search, Plus, MoreHorizontal,
} from "lucide-react";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "../ui/dropdown-menu";
import api from "../../lib/api";
import { initials } from "../../lib/format";

/**
 * §1, implemented in the reference's exact order:
 *
 *   1. Logo wordmark
 *   2. Global ⌘K search field
 *   3. Primary CTA
 *   4. Ungrouped primary nav
 *   5..8. Small-caps low-contrast section headers grouping the remainder
 *   9. Sticky footer: account row (avatar initials, name, "···" overflow menu)
 *
 * Two deliberate changes from the previous sidebar:
 *
 * - Fixed at 260px with no collapse control. §1 is explicit: "there's no
 *   collapse/accordion, just a plain overflow scroll region with a thin custom
 *   scrollbar." The collapsed/onToggle props are therefore gone.
 * - The account row's overflow menu hangs off a "···" button rather than the
 *   whole row being the trigger, which is what §1 describes and what §4.11
 *   shows.
 *
 * Identity resolution is untouched — Clerk first, api.getCurrentUser() as the
 * mock-mode fallback, api.getWorkspace() for the slug. No data call changed.
 */

const PRIMARY_NAV = [
    { to: "/dashboard", label: "Dashboard", icon: Home,          testId: "nav-dashboard" },
    { to: "/memories",  label: "Memories",  icon: Brain,         testId: "nav-memories"  },
    { to: "/conflicts", label: "Conflicts", icon: AlertTriangle, testId: "nav-conflicts" },
];

/* §1's grouping structure, mapped onto SourceMind's real sections. */
const NAV_GROUPS = [
    {
        label: "Analytics",
        items: [
            { to: "/analytics", label: "Analytics", icon: BarChart3,      testId: "nav-analytics" },
            { to: "/handoff",   label: "Handoff",   icon: ArrowRightLeft, testId: "nav-handoff"   },
        ],
    },
    {
        label: "Data",
        items: [
            { to: "/connectors", label: "Connectors", icon: Plug, testId: "nav-connectors" },
        ],
    },
    {
        label: "Workspace",
        items: [
            { to: "/settings", label: "Settings", icon: SettingsIcon, testId: "nav-settings" },
        ],
    },
];

function navClass({ isActive }) {
    /* §1: "Active nav item = subtle filled rounded rectangle behind the row,
       plus the icon/label switching from gray to white/blue." A filled
       rectangle, not a border — the previous implementation used a 1px blue
       border, which reads as an outline rather than a fill. */
    return [
        "group flex items-center gap-3 px-3 h-navitem rounded-md text-body font-medium",
        "transition-colors sm-focusable",
        isActive
            ? "bg-brand/10 text-content"
            : "text-content-secondary hover:text-content hover:bg-white/[0.03]",
    ].join(" ");
}

function NavRow({ item }) {
    return (
        <NavLink to={item.to} data-testid={item.testId} className={navClass}>
            {({ isActive }) => (
                <>
                    <item.icon
                        className={`w-4 h-4 shrink-0 ${isActive ? "text-brand" : ""}`}
                        strokeWidth={2}
                        aria-hidden="true"
                    />
                    <span className="truncate">{item.label}</span>
                </>
            )}
        </NavLink>
    );
}

export default function Sidebar() {
    const navigate = useNavigate();

    const { isSignedIn, user } = useUser();
    const { signOut } = useClerk();
    const [fallbackUser, setFallbackUser] = useState(null);
    const [workspace, setWorkspace] = useState(null);

    useEffect(() => {
        if (isSignedIn) return;
        api.getCurrentUser().then(setFallbackUser).catch(() => setFallbackUser(null));
    }, [isSignedIn]);

    useEffect(() => {
        api.getWorkspace().then(setWorkspace).catch(() => setWorkspace(null));
    }, []);

    const userName =
        user?.fullName ||
        fallbackUser?.display_name ||
        fallbackUser?.name ||
        "Signed out";
    const userEmail =
        user?.primaryEmailAddress?.emailAddress || fallbackUser?.email || "";
    const avatarUrl = user?.imageUrl || fallbackUser?.avatar_url || null;

    // No confirmation step: signing out is reversible, unlike the delete flows
    // the confirm-before-destructive rule is meant for.
    //
    // In mock mode there is no Clerk session to end - the demo is browsable
    // signed-out by design - so this just returns to the landing page. Clerk's
    // own afterSignOutUrl (set in index.js) already carries the basename.
    const handleSignOut = async () => {
        if (isSignedIn) {
            await signOut();
            return;
        }
        navigate("/");
    };

    return (
        <aside
            data-testid="sidebar"
            className="w-sidebar shrink-0 h-screen sticky top-0 z-30 border-r border-hairline bg-surface flex flex-col"
        >
            {/* 1 — logo wordmark */}
            <div className="px-5 pt-5 pb-4">
                <button
                    onClick={() => navigate("/dashboard")}
                    className="flex items-center gap-2.5 sm-focusable rounded-md"
                    aria-label="SourceMind — go to dashboard"
                >
                    {/* §2: product chrome is monochrome; the old mark was a
                        blue→purple gradient, i.e. a second accent hue. */}
                    <div className="w-6 h-6 rounded-md bg-brand flex items-center justify-center shrink-0">
                        <Brain className="w-3.5 h-3.5 text-white" strokeWidth={2.5} aria-hidden="true" />
                    </div>
                    <span className="text-[15px] font-semibold text-content tracking-tight">
                        SourceMind
                    </span>
                </button>
            </div>

            {/* 2 — global search. ⌘K affordance per §1. */}
            <div className="px-3 pb-3">
                <button
                    data-testid="sidebar-search"
                    onClick={() => navigate("/memories")}
                    className="w-full h-9 px-3 rounded-md border border-hairline bg-surface-page
                               flex items-center gap-2 text-content-muted
                               hover:border-hairline-hover hover:text-content-secondary
                               transition-colors sm-focusable"
                >
                    <Search className="w-3.5 h-3.5 shrink-0" strokeWidth={2} aria-hidden="true" />
                    <span className="text-body flex-1 text-left">Search memories</span>
                    <kbd className="font-mono text-[10px] text-content-muted border border-hairline rounded px-1 py-0.5">
                        ⌘K
                    </kbd>
                </button>
            </div>

            {/* 3 — primary CTA */}
            <div className="px-3 pb-4">
                <button
                    data-testid="sidebar-cta"
                    onClick={() => navigate("/connectors")}
                    className="w-full h-9 rounded-md bg-brand-fill hover:bg-brand-fill-hover
                               text-white text-body font-medium
                               flex items-center justify-center gap-1.5
                               transition-colors sm-focusable"
                >
                    <Plus className="w-4 h-4" strokeWidth={2.5} aria-hidden="true" />
                    Add source
                </button>
            </div>

            {/* 4–8 — nav. §1: "The sidebar scrolls independently of the main
                content ... a plain overflow scroll region." */}
            <nav className="flex-1 min-h-0 overflow-y-auto px-3 pb-4 space-y-1">
                {PRIMARY_NAV.map((item) => (
                    <NavRow key={item.to} item={item} />
                ))}

                {NAV_GROUPS.map((group) => (
                    <div key={group.label} className="pt-5">
                        {/* §1: "small-caps, wide letter-spacing, low-contrast
                            gray ... a common pattern for grouping 2–3 items
                            without a full visual divider." */}
                        <div className="sm-micro-label px-3 pb-1.5">{group.label}</div>
                        <div className="space-y-1">
                            {group.items.map((item) => (
                                <NavRow key={item.to} item={item} />
                            ))}
                        </div>
                    </div>
                ))}
            </nav>

            {/* 9 — sticky footer account row */}
            <div className="shrink-0 border-t border-hairline p-3">
                <div
                    data-testid="sidebar-user"
                    className="flex items-center gap-2.5 px-1"
                >
                    {avatarUrl ? (
                        <img
                            src={avatarUrl}
                            alt=""
                            className="w-7 h-7 rounded-full shrink-0 object-cover"
                        />
                    ) : (
                        /* §1 specifies avatar INITIALS. §2 keeps chrome
                           monochrome, so this is a neutral surface rather than
                           a per-user colour. */
                        <div className="w-7 h-7 rounded-full shrink-0 flex items-center justify-center
                                        bg-surface-hover border border-hairline
                                        text-[10px] font-semibold text-content-secondary">
                            {initials(userName)}
                        </div>
                    )}

                    <div className="flex-1 min-w-0">
                        <div className="text-body font-medium text-content truncate leading-tight">
                            {userName}
                        </div>
                        {userEmail && (
                            <div className="text-[11px] text-content-secondary truncate leading-tight">
                                {userEmail}
                            </div>
                        )}
                    </div>

                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <button
                                type="button"
                                data-testid="sidebar-user-menu"
                                aria-label={`Account menu for ${userName}`}
                                className="w-7 h-7 shrink-0 rounded-md flex items-center justify-center
                                           text-content-secondary hover:text-content hover:bg-white/[0.04]
                                           transition-colors sm-focusable"
                            >
                                <MoreHorizontal className="w-4 h-4" strokeWidth={2} aria-hidden="true" />
                            </button>
                        </DropdownMenuTrigger>

                        <DropdownMenuContent
                            side="top"
                            align="end"
                            sideOffset={8}
                            className="w-56 bg-surface border-hairline text-content"
                        >
                            <DropdownMenuLabel className="font-normal">
                                <div className="text-body font-medium truncate">{userName}</div>
                                {userEmail && (
                                    <div className="text-[11px] text-content-secondary truncate">
                                        {userEmail}
                                    </div>
                                )}
                            </DropdownMenuLabel>
                            <DropdownMenuSeparator className="bg-hairline" />
                            <DropdownMenuItem
                                onSelect={() => navigate("/settings")}
                                className="cursor-pointer text-body"
                            >
                                <SettingsIcon className="w-4 h-4 mr-2" strokeWidth={2} aria-hidden="true" />
                                Account settings
                            </DropdownMenuItem>
                            <DropdownMenuSeparator className="bg-hairline" />
                            {/* §2/§4.11: red is reserved for destructive and
                                sign-out; never decorative. */}
                            <DropdownMenuItem
                                data-testid="sidebar-signout"
                                onSelect={handleSignOut}
                                className="text-danger focus:text-danger focus:bg-danger/10 cursor-pointer text-body"
                            >
                                <LogOut className="w-4 h-4 mr-2" strokeWidth={2} aria-hidden="true" />
                                Sign out
                            </DropdownMenuItem>
                        </DropdownMenuContent>
                    </DropdownMenu>
                </div>
            </div>
        </aside>
    );
}
