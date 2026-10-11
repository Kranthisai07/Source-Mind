import React from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";

import Landing       from "@/pages/Landing";
import Develop       from "@/pages/Develop";
import AuthPage      from "@/pages/AuthPage";
import AppLayout     from "@/components/layout/AppLayout";
import Dashboard     from "@/pages/Dashboard";
import Memories      from "@/pages/Memories";
import MemoryDetail  from "@/pages/MemoryDetail";
import Analytics     from "@/pages/Analytics";
import Conflicts     from "@/pages/Conflicts";
import ConflictDetail from "@/pages/ConflictDetail";
import Handoff       from "@/pages/Handoff";
import Connectors    from "@/pages/Connectors";
import Settings      from "@/pages/Settings";
import PilotTools    from "@/pages/PilotTools";
import { ClerkTokenBridge, RequireAuth } from "@/components/auth/ClerkAuthBridge";
import { isPilotMode } from "@/lib/pilotMode";

export default function App() {
    const pilotMode = isPilotMode();
    const pilotRestricted = (page) => pilotMode ? <Navigate to="/memories" replace /> : page;
    return (
        <div className="App min-h-screen bg-sm-bg text-sm-text">
            <BrowserRouter basename={process.env.PUBLIC_URL}>
                <ClerkTokenBridge />
                <Routes>
                    <Route path="/" element={<Landing />} />
                    <Route path="/develop" element={<Develop />} />
                    <Route path="/sign-in" element={<AuthPage mode="sign-in" />} />
                    <Route path="/sign-up" element={<AuthPage mode="sign-up" />} />

                    <Route element={<RequireAuth><AppLayout /></RequireAuth>}>
                        <Route path="/dashboard"          element={pilotRestricted(<Dashboard />)} />
                        <Route path="/memories"           element={<Memories />} />
                        <Route path="/memories/:id"       element={<MemoryDetail />} />
                        <Route path="/analytics"          element={pilotRestricted(<Analytics />)} />
                        <Route path="/conflicts"          element={pilotRestricted(<Conflicts />)} />
                        <Route path="/conflicts/:id"      element={pilotRestricted(<ConflictDetail />)} />
                        <Route path="/handoff"            element={pilotRestricted(<Handoff />)} />
                        <Route path="/connectors"         element={pilotRestricted(<Connectors />)} />
                        <Route path="/settings"           element={<Settings />} />
                        <Route path="/pilot"              element={pilotMode ? <PilotTools /> : <Navigate to="/memories" replace />} />
                    </Route>

                    <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
            </BrowserRouter>
            <Toaster
                theme="dark"
                position="bottom-right"
                toastOptions={{
                    style: {
                        background: "var(--sm-surface)",
                        border: "1px solid var(--sm-border)",
                        color: "var(--sm-text)",
                        fontFamily: "'DM Sans', sans-serif",
                    },
                }}
            />
        </div>
    );
}
