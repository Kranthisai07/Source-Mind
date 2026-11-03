import StandaloneFrame from "../brand/StandaloneFrame";
import React, { useState } from "react";
import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";
import { PageLoadError, PageLoading } from "../widgets/PageLoadState";
import NoWorkspaceState from "../workspace/NoWorkspaceState";
import { WorkspaceProvider, useWorkspaces } from "../workspace/WorkspaceProvider";
import { useWorkspaceScope } from "../../hooks/useWorkspaceScope";

export default function AppLayout() {
    return (
        <WorkspaceProvider>
            <Shell />
        </WorkspaceProvider>
    );
}

function Shell() {
    const [collapsed, setCollapsed] = useState(false);
    const { status, error, reload } = useWorkspaces();
    // Moves on every workspace switch. Keying the page outlet on it discards
    // the previous workspace's page state, timers and refs wholesale.
    const scope = useWorkspaceScope();

    if (status === "loading") {
        return (
            <StandaloneFrame testId="workspaces-loading-frame" width="max-w-xl">
                <PageLoading label="Loading your workspaces" testId="workspaces-loading" />
            </StandaloneFrame>
        );
    }
    if (status === "error") {
        // A failed lookup is shown as a failure, never as "no workspaces".
        return (
            <StandaloneFrame testId="workspaces-error-frame" width="max-w-xl">
                <PageLoadError error={error} onRetry={reload} testId="workspaces-error" />
            </StandaloneFrame>
        );
    }
    if (status === "empty") return <NoWorkspaceState />;

    return (
        <div className="flex min-h-screen bg-sm-bg text-sm-text">
            <Sidebar collapsed={collapsed} onToggle={() => setCollapsed(v => !v)} />
            <main className="flex-1 min-w-0 flex flex-col">
                <Outlet key={scope} />
            </main>
        </div>
    );
}
