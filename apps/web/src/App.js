import React from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";

import Landing       from "@/pages/Landing";
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

export default function App() {
    return (
        <div className="App min-h-screen bg-sm-bg text-sm-text">
            <BrowserRouter>
                <Routes>
                    <Route path="/" element={<Landing />} />
                    <Route path="/sign-in" element={<AuthPage mode="sign-in" />} />
                    <Route path="/sign-up" element={<AuthPage mode="sign-up" />} />

                    <Route element={<AppLayout />}>
                        <Route path="/dashboard"          element={<Dashboard />} />
                        <Route path="/memories"           element={<Memories />} />
                        <Route path="/memories/:id"       element={<MemoryDetail />} />
                        <Route path="/analytics"          element={<Analytics />} />
                        <Route path="/conflicts"          element={<Conflicts />} />
                        <Route path="/conflicts/:id"      element={<ConflictDetail />} />
                        <Route path="/handoff"            element={<Handoff />} />
                        <Route path="/connectors"         element={<Connectors />} />
                        <Route path="/settings"           element={<Settings />} />
                    </Route>

                    <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
            </BrowserRouter>
            <Toaster
                theme="dark"
                position="bottom-right"
                toastOptions={{
                    style: {
                        background: "#12121A",
                        border: "1px solid #1E1E2E",
                        color: "#E8E8F0",
                        fontFamily: "Inter, sans-serif",
                    },
                }}
            />
        </div>
    );
}
