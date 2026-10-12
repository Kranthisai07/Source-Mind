import React, { useEffect, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useClerk, useUser } from "@clerk/clerk-react";
import {
    Home, Brain, BarChart3, AlertTriangle, ArrowRightLeft,
    Plug, Settings as SettingsIcon, ChevronLeft, ChevronRight, LogOut,
    ChevronsUpDown, Check, Plus,
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
import { isPilotMode } from "../../lib/pilotMode";
import { useWorkspaces } from "../workspace/WorkspaceProvider";
import CreateWorkspaceDialog from "../workspace/CreateWorkspaceDialog";

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
    const pilotMode = isPilotMode();
    const navigate = useNavigate();
    const { isSignedIn, user } = useUser();
    const { signOut } = useClerk();

    const [fallbackUser, setFallbackUser] = useState(null);
    const [createOpen, setCreateOpen] = useState(false);
    const { list: workspaces, currentId, switchTo } = useWorkspaces();
    const workspace = workspaces.find((w) => w.id === currentId) || null;

    useEffect(() => {
        if (isSignedIn) {
            setFallbackUser(null);
            return;
        }
        api.getCurrentUser().then(setFallbackUser).catch(() => setFallbackUser(null));
    }, [isSignedIn]);

    const userName = user?.fullName || fallbackUser?.display_name || fallbackUser?.name || "Signed out";
    const userEmail = user?.primaryEmailAddress?.emailAddress || fallbackUser?.email || "";
    const avatarUrl = user?.imageUrl || fallbackUser?.avatar_url || null;
    const avatarColor = fallbackUser?.avatarColor || "#4F7EFF";
    const wsName = workspace?.name || "";
    const wsSlug = workspace?.slug || "";

    const handleSignOut = async () => {
        if (isSignedIn) {
            await signOut();
            return;
        }
        navigate("/");
    };

    const width = collapsed ? "w-[64px]" : "w-[220px]";
    return (
        <aside
            data-testid="sidebar"
            className={`${width} shrink-0 h-screen sticky top-0 z-30 border-r border-sm-border bg-sm-surface/60 backdrop-blur-xl transition-[width] duration-200 flex flex-col`}
        >
            {/* Logo / workspace */}
            <div className="h-16 flex items-center px-3 border-b border-sm-border">
                {/* modal={false}: the "Create workspace" item opens a Dialog; a modal
                    menu would leave the page inert after handing focus over. */}
                <DropdownMenu modal={false}>
                    <DropdownMenuTrigger asChild>
                        <button
                            type="button"
                            data-testid="workspace-switcher"
                            aria-label={`Workspace: ${wsName || "none"}. Switch workspace`}
                            className="flex items-center gap-2.5 w-full overflow-hidden rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sm-blue/50"
                            title={wsName}
                        >
                            <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-sm-blue to-sm-purple flex items-center justify-center shrink-0 shadow-[0_0_0_1px_rgba(79,126,255,0.35)_inset]">
                                <Brain className="w-[18px] h-[18px] text-white" strokeWidth={2.5} />
                            </div>
                            {!collapsed && (
                                <>
                                    <div className="flex-1 text-left overflow-hidden">
                                        <div className="text-[13px] font-semibold text-sm-text truncate leading-tight">{wsName || "SourceMind"}</div>
                                        <div className="text-[11px] font-mono text-sm-text-secondary truncate leading-tight">{wsSlug}</div>
                                    </div>
                                    <ChevronsUpDown className="w-4 h-4 text-sm-text-secondary shrink-0" />
                                </>
                            )}
                        </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent
                        side={collapsed ? "right" : "bottom"}
                        align="start"
                        sideOffset={8}
                        data-testid="workspace-menu"
                        className="w-64 max-h-80 overflow-y-auto bg-sm-surface border-sm-border text-sm-text"
                    >
                        <DropdownMenuLabel className="text-[11px] font-medium text-sm-text-secondary">Workspaces</DropdownMenuLabel>
                        {workspaces.map((w) => (
                            <DropdownMenuItem
                                key={w.id}
                                data-testid={`workspace-option-${w.id}`}
                                onSelect={() => switchTo(w.id)}
                                className="cursor-pointer gap-2"
                            >
                                <div className="flex-1 min-w-0">
                                    <div className="text-[12.5px] font-medium truncate">{w.name}</div>
                                    <div className="text-[11px] font-mono text-sm-text-secondary truncate">{w.slug}</div>
                                </div>
                                {w.id === currentId && <Check className="w-4 h-4 text-sm-blue shrink-0" aria-label="Current workspace" />}
                            </DropdownMenuItem>
                        ))}
                        <DropdownMenuSeparator className="bg-sm-border" />
                        <DropdownMenuItem
                            data-testid="workspace-create-item"
                            onSelect={() => setCreateOpen(true)}
                            className="cursor-pointer gap-2 text-[12.5px]"
                        >
                            <Plus className="w-4 h-4" /> Create workspace
                        </DropdownMenuItem>
                    </DropdownMenuContent>
                </DropdownMenu>
                <CreateWorkspaceDialog open={createOpen} onOpenChange={setCreateOpen} />
            </div>

            {/* Nav */}
            <nav className="flex-1 p-2 space-y-0.5 overflow-y-auto">
                {NAV.filter((item) => !pilotMode || ["/memories", "/settings"].includes(item.to)).map((item) => (
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
                <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                        <button
                            type="button"
                            data-testid="sidebar-user"
                            title={collapsed ? userName : undefined}
                            aria-label={`Account menu for ${userName}`}
                            className={`w-full flex items-center gap-2.5 px-2 h-11 rounded-lg text-left
                                hover:bg-white/[0.03] focus-visible:outline-none
                                focus-visible:ring-2 focus-visible:ring-sm-blue/50
                                transition-colors ${collapsed ? "justify-center" : ""}`}
                        >
                            {avatarUrl ? (
                                <img src={avatarUrl} alt="" className="w-8 h-8 rounded-full shrink-0 object-cover" />
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
                        </button>
                    </DropdownMenuTrigger>

                    <DropdownMenuContent
                        side="top"
                        align="start"
                        sideOffset={8}
                        className="w-56 bg-sm-surface border-sm-border text-sm-text"
                    >
                        <DropdownMenuLabel className="font-normal">
                            <div className="text-[12.5px] font-medium truncate">{userName}</div>
                            {userEmail && <div className="text-[11px] text-sm-text-secondary truncate">{userEmail}</div>}
                        </DropdownMenuLabel>
                        <DropdownMenuSeparator className="bg-sm-border" />
                        <DropdownMenuItem
                            data-testid="sidebar-signout"
                            onSelect={handleSignOut}
                            className="text-sm-red focus:text-sm-red focus:bg-sm-red/10 cursor-pointer"
                        >
                            <LogOut className="w-4 h-4 mr-2" strokeWidth={2} />
                            Sign out
                        </DropdownMenuItem>
                    </DropdownMenuContent>
                </DropdownMenu>
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
