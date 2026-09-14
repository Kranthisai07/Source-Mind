// Two small pieces of glue between Clerk and the rest of the app.
//
// ClerkTokenBridge  — publishes Clerk's getToken() to the plain-module API
//                     client (see lib/authToken.js for why the seam exists).
// RequireAuth       — gates the authenticated shell.
//
// Both are deliberately inert in mock mode. The demo has always been browsable
// without an account, and making mock mode require a real Clerk sign-in would
// break that for no gain — mockApi never sends a token anyway.

import React, { useEffect, useRef } from "react";
import { useAuth } from "@clerk/clerk-react";
import { Navigate, useLocation } from "react-router-dom";

import { useMocks, resetApiCaches } from "@/lib/api";
import { setTokenGetter } from "@/lib/authToken";

export function ClerkTokenBridge() {
    const { getToken, isLoaded, userId } = useAuth();
    const previousUserId = useRef(undefined);

    useEffect(() => {
        if (!isLoaded) return;
        setTokenGetter(getToken);
        return () => setTokenGetter(null);
    }, [getToken, isLoaded]);

    // Clear identity-scoped caches whenever the signed-in user changes,
    // including sign-out (userId -> null) and a switch from A to B.
    //
    // Is that reachable today? No: ClerkProvider is configured without
    // routerPush/routerReplace, so Clerk navigates with window.location and a
    // sign-out reloads the document, discarding module state wholesale. The
    // guard is here because that is an incidental property of how Clerk is
    // configured, not something this app asserts. Supplying router props — the
    // ordinary Clerk + React Router integration, and a change someone would
    // make for entirely unrelated reasons — turns sign-out into an SPA
    // navigation and `_wsPromise` would then survive into the next session,
    // handing user B user A's workspace id.
    useEffect(() => {
        if (!isLoaded) return;
        if (previousUserId.current === undefined) {
            previousUserId.current = userId ?? null;
            return;
        }
        if (previousUserId.current !== (userId ?? null)) {
            previousUserId.current = userId ?? null;
            resetApiCaches();
        }
    }, [isLoaded, userId]);

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
