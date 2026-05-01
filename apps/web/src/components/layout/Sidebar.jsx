import React from "react";
import { NavLink, useNavigate } from "react-router-dom";
import {
    Home, Brain, BarChart3, AlertTriangle, ArrowRightLeft,
    Plug, Settings as SettingsIcon, ChevronLeft, ChevronRight,
} from "lucide-react";
import { CURRENT_USER, WORKSPACE } from "../../lib/mockData";
import { initials } from "../../lib/format";

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
                    title={WORKSPACE.name}
                >
                    <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-sm-blue to-sm-purple flex items-center justify-center shrink-0 shadow-[0_0_0_1px_rgba(79,126,255,0.35)_inset]">
                        <Brain className="w-[18px] h-[18px] text-white" strokeWidth={2.5} />
                    </div>
                    {!collapsed && (
                        <div className="flex-1 text-left overflow-hidden">
                            <div className="text-[13px] font-semibold text-sm-text truncate leading-tight">SourceMind</div>
                            <div className="text-[11px] font-mono text-sm-text-secondary truncate leading-tight">{WORKSPACE.slug}</div>
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
                    <div
                        className="w-8 h-8 rounded-full flex items-center justify-center text-[11px] font-semibold shrink-0 text-white"
                        style={{ background: CURRENT_USER.avatarColor }}
                    >
                        {initials(CURRENT_USER.name)}
                    </div>
                    {!collapsed && (
                        <div className="flex-1 overflow-hidden">
                            <div className="text-[12.5px] font-medium text-sm-text truncate leading-tight">{CURRENT_USER.name}</div>
                            <div className="text-[11px] text-sm-text-secondary truncate leading-tight">{CURRENT_USER.email}</div>
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
