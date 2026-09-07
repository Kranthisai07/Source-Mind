import React, { useEffect, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useUser } from "@clerk/clerk-react";
import {
    Home, Brain, BarChart3, AlertTriangle, ArrowRightLeft,
    Plug, Settings as SettingsIcon, ChevronLeft, ChevronRight,
} from "lucide-react";
import api from "../../lib/api";
import { initials } from "../../lib/format";

// CURRENT_USER and WORKSPACE came from mockData and were rendered as the
// persistent chrome on every authenticated page: a fabricated name, email and
// workspace slug sitting beside real data, while Clerk held the actual signed-in
// user. Both are now resolved live.
//
// Clerk is the source for identity because it already has the session with no
// extra request. api.getCurrentUser() is the fallback rather than the primary,
// because mock mode is deliberately browsable without signing in (RequireAuth
// is inert there), so useUser() reports nobody and mockApi supplies the demo
// identity instead.

const NAV = [
    { to: "/dashboard",  label: "Dashboard",   icon: Home,           testId: "nav-dashboard"  },
    { to: "/memories",   label: "Memories",    icon: Brain,          testId: "nav-memories"   },
    { to: "/analytics",  label: "Analytics",   icon: BarChart3,      testId: "nav-analytics"  },
    { to: "/conflicts",  label: "Conflicts",   icon: AlertTriangle,  testId: "nav-conflicts"  },
    { to: "/handoff",    label: "Handoff",     icon: ArrowRightLeft, testId: "nav-handoff"    },
    { to: "/connectors", label: "Connectors",  icon: Plug,           testId: "nav-connectors" },
    { to: "/settings",   label: "Settings",    icon: SettingsIcon,   testId: "nav-settings"   },
];

export default function Sidebar({ collapsed, onToggle }) {
    const navigate = useNavigate();

    const { isSignedIn, user } = useUser();
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
    // The real user record carries no avatar colour; mockData did.
    const avatarColor = fallbackUser?.avatarColor || "#4F7EFF";
    const wsName = workspace?.name || "";
    const wsSlug = workspace?.slug || "";
    const width = collapsed ? "w-[64px]" : "w-[220px]";
    return (
        <aside
            data-testid="sidebar"
            className={`${width} shrink-0 h-screen sticky top-0 z-30 border-r border-sm-border bg-sm-surface/60 backdrop-blur-xl transition-[width] duration-200 flex flex-col`}
        >
            {/* Logo / workspace */}
            <div className="h-16 flex items-center px-3 border-b border-sm-border">
                <button
                    data-testid="workspace-switcher"
                    onClick={() => navigate("/dashboard")}
                    className="flex items-center gap-2.5 w-full overflow-hidden"
                    title={wsName}
                >
                    <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-sm-blue to-sm-purple flex items-center justify-center shrink-0 shadow-[0_0_0_1px_rgba(79,126,255,0.35)_inset]">
                        <Brain className="w-[18px] h-[18px] text-white" strokeWidth={2.5} />
                    </div>
                    {!collapsed && (
                        <div className="flex-1 text-left overflow-hidden">
                            <div className="text-[13px] font-semibold text-sm-text truncate leading-tight">SourceMind</div>
                            <div className="text-[11px] font-mono text-sm-text-secondary truncate leading-tight">{wsSlug}</div>
                        </div>
                    )}
                </button>
            </div>

            {/* Nav */}
            <nav className="flex-1 p-2 space-y-0.5 overflow-y-auto">
                {NAV.map((item) => (
                    <NavLink
                        key={item.to}
                        to={item.to}
                        data-testid={item.testId}
                        className={({ isActive }) =>
                            [
                                "group flex items-center gap-3 px-3 h-9 rounded-lg text-[13px] font-medium transition-colors",
                                isActive
                                    ? "bg-sm-blue/10 text-sm-text border border-sm-blue/30 shadow-[0_0_0_1px_rgba(79,126,255,0.05)]"
                                    : "text-sm-text-secondary hover:text-sm-text hover:bg-white/[0.03] border border-transparent",
                                collapsed ? "justify-center px-0" : "",
                            ].join(" ")
                        }
                        title={collapsed ? item.label : undefined}
                    >
                        <item.icon className="w-4 h-4 shrink-0" strokeWidth={2} />
                        {!collapsed && <span className="truncate">{item.label}</span>}
                    </NavLink>
                ))}
            </nav>

            {/* User + collapse */}
            <div className="p-2 border-t border-sm-border space-y-1">
                <div
                    data-testid="sidebar-user"
                    className={`flex items-center gap-2.5 px-2 h-11 rounded-lg ${collapsed ? "justify-center" : ""}`}
                >
                    {avatarUrl ? (
                        <img
                            src={avatarUrl}
                            alt=""
                            className="w-8 h-8 rounded-full shrink-0 object-cover"
                        />
                    ) : (
                        <div
                            className="w-8 h-8 rounded-full flex items-center justify-center text-[11px] font-semibold shrink-0 text-white"
                            style={{ background: avatarColor }}
                        >
                            {initials(userName)}
                        </div>
                    )}
                    {!collapsed && (
                        <div className="flex-1 overflow-hidden">
                            <div className="text-[12.5px] font-medium text-sm-text truncate leading-tight">{userName}</div>
                            <div className="text-[11px] text-sm-text-secondary truncate leading-tight">{userEmail}</div>
                        </div>
                    )}
                </div>
                <button
                    data-testid="sidebar-toggle"
                    onClick={onToggle}
                    className={`w-full h-8 rounded-lg text-sm-text-secondary hover:text-sm-text hover:bg-white/[0.03] flex items-center ${collapsed ? "justify-center" : "justify-end px-3"} transition-colors`}
                    title={collapsed ? "Expand" : "Collapse"}
                >
                    {collapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />}
                </button>
            </div>
        </aside>
    );
}
