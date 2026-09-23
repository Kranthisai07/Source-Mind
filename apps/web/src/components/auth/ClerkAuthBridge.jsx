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
import { setCurrentUserId } from "@/lib/currentUser";

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
        const settled = userId ?? null;

        // Published on every settled render, not only on change: a plain
        // module cannot read Clerk's context, and a submission key minted
        // before this ran would otherwise carry a null user for the life of
        // the panel.
        //
        // This has to happen BEFORE the first-render bookkeeping below. It
        // used to sit after an early return taken on the very first settled
        // render, so the ordinary case — sign in, never switch account —
        // never published at all and every submission key carried a null
        // user. Only a later identity change reached it, which is the one
        // case the comment above was not written for.
        setCurrentUserId(settled);

        // First settled render: record the baseline, but do NOT treat it as a
        // change. Resetting here would throw away the workspace cache on every
        // mount.
        if (previousUserId.current === undefined) {
            previousUserId.current = settled;
            return;
        }
        if (previousUserId.current !== settled) {
            previousUserId.current = settled;
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
