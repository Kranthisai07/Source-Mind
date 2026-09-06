// Two small pieces of glue between Clerk and the rest of the app.
//
// ClerkTokenBridge  — publishes Clerk's getToken() to the plain-module API
//                     client (see lib/authToken.js for why the seam exists).
// RequireAuth       — gates the authenticated shell.
//
// Both are deliberately inert in mock mode. The demo has always been browsable
// without an account, and making mock mode require a real Clerk sign-in would
// break that for no gain — mockApi never sends a token anyway.

import React, { useEffect } from "react";
import { useAuth } from "@clerk/clerk-react";
import { Navigate, useLocation } from "react-router-dom";

import { useMocks } from "@/lib/api";
import { setTokenGetter } from "@/lib/authToken";

export function ClerkTokenBridge() {
    const { getToken, isLoaded } = useAuth();

    useEffect(() => {
        if (!isLoaded) return;
        setTokenGetter(getToken);
        return () => setTokenGetter(null);
    }, [getToken, isLoaded]);

    return null;
}

export function RequireAuth({ children }) {
    const { isLoaded, isSignedIn } = useAuth();
    const location = useLocation();

    // Mock mode: no backend, no token, no reason to gate.
    if (useMocks) return children;

    // Clerk resolves the session asynchronously. Rendering the app during that
    // window would fire API calls with no token and get a burst of 401s, and
    // redirecting would bounce an already-signed-in user to the sign-in page on
    // every refresh.
    if (!isLoaded) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-sm-bg">
                <div className="text-sm-text-secondary text-[13px]">Loading session…</div>
            </div>
        );
    }

    if (!isSignedIn) {
        return <Navigate to="/sign-in" replace state={{ from: location.pathname }} />;
    }

    return children;
}
