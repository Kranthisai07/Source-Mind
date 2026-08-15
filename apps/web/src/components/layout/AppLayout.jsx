import React, { useState } from "react";
import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";

export default function AppLayout() {
    const [collapsed, setCollapsed] = useState(false);
    return (
        <div className="flex min-h-screen bg-sm-bg text-sm-text">
            <Sidebar collapsed={collapsed} onToggle={() => setCollapsed(v => !v)} />
            <main className="flex-1 min-w-0 flex flex-col">
                <Outlet />
            </main>
        </div>
    );
}
