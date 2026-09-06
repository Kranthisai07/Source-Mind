// Real Clerk sign-in / sign-up.
//
// This page previously rendered a hand-built form whose submit handler called
// navigate("/dashboard") — no credentials were ever checked, and the
// "PROTECTED BY CLERK" footer was decorative. It is now Clerk's own component,
// so the session it creates is a real one that getToken() can mint JWTs from.
//
// routing="virtual" keeps Clerk's multi-step flows (SSO callback, second
// factor, password reset) inside this component instead of pushing extra URL
// segments. That matters here because BrowserRouter runs under
// basename={PUBLIC_URL}, which is "" in dev but "/Source-Mind" on GitHub Pages;
// path-based Clerk routing would need to know that prefix and would break on
// exactly one of the two deployments.

import React from "react";
import { Link } from "react-router-dom";
import { SignIn, SignUp } from "@clerk/clerk-react";
import { Brain } from "lucide-react";

import { appUrl } from "@/lib/appUrl";

// Clerk's default theme is light; the rest of the app is dark. These map
// Clerk's slots onto the existing SourceMind palette so the page does not
// flash white between routes.
const clerkAppearance = {
    variables: {
        colorPrimary: "#4F7EFF",
        colorBackground: "#12121A",
        colorText: "#E8E8F0",
        colorTextSecondary: "#9494A8",
        colorInputBackground: "#0A0A0F",
        colorInputText: "#E8E8F0",
        borderRadius: "0.75rem",
        fontFamily: "Inter, sans-serif",
    },
    elements: {
        rootBox: "w-full",
        card: "bg-transparent shadow-none border-0 p-0",
        headerTitle: "text-[22px] font-semibold tracking-tight",
        headerSubtitle: "text-[12.5px]",
        footer: "hidden",
    },
};

export default function AuthPage({ mode = "sign-in" }) {
    const isSignUp = mode === "sign-up";

    return (
        <div className="min-h-screen bg-sm-bg relative flex items-center justify-center p-6 overflow-hidden">
            <div className="absolute inset-0 bg-grid bg-grid-fade opacity-30" />
            <div className="absolute inset-x-0 top-0 h-[520px] bg-[radial-gradient(ellipse_60%_40%_at_50%_0%,rgba(79,126,255,0.15),transparent_70%)] pointer-events-none" />

            <div className="relative w-full max-w-[420px]">
                <Link
                    to="/"
                    className="flex items-center justify-center gap-2.5 mb-8 group"
                    data-testid="auth-logo-link"
                >
                    <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-sm-blue to-sm-purple flex items-center justify-center shadow-[0_8px_24px_-8px_rgba(79,126,255,0.6)] group-hover:scale-105 transition-transform">
                        <Brain className="w-5 h-5 text-white" strokeWidth={2.5} />
                    </div>
                    <span className="text-[18px] font-semibold tracking-tight text-sm-text">
                        SourceMind
                    </span>
                </Link>
                <p className="text-center text-sm-text-secondary text-[13px] mb-10">
                    Your team's collective memory
                </p>

                <div className="glass rounded-2xl p-8" data-testid="clerk-auth-card">
                    {isSignUp ? (
                        <SignUp
                            routing="virtual"
                            appearance={clerkAppearance}
                            signInUrl={appUrl("/sign-in")}
                            forceRedirectUrl={appUrl("/dashboard")}
                        />
                    ) : (
                        <SignIn
                            routing="virtual"
                            appearance={clerkAppearance}
                            signUpUrl={appUrl("/sign-up")}
                            forceRedirectUrl={appUrl("/dashboard")}
                        />
                    )}

                    <div className="mt-6 text-center text-[12px] text-sm-text-secondary">
                        {isSignUp ? (
                            <>
                                Already have an account?{" "}
                                <Link to="/sign-in" className="text-sm-blue hover:underline" data-testid="switch-to-signin">
                                    Sign in
                                </Link>
                            </>
                        ) : (
                            <>
                                Don't have an account?{" "}
                                <Link to="/sign-up" className="text-sm-blue hover:underline" data-testid="switch-to-signup">
                                    Sign up
                                </Link>
                            </>
                        )}
                    </div>
                </div>

                <p className="text-center text-[11px] font-mono text-sm-text-muted mt-6 tracking-wider">
                    PROTECTED BY CLERK
                </p>
            </div>
        </div>
    );
}
