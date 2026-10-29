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

import { appUrl } from "@/lib/appUrl";
import { SiteHeader, SiteFooter, useSitePage } from "@/components/marketing/SiteChrome";
import "@/components/marketing/marketing.css";

// Same page chrome and --site-* tokens as the landing page, so Clerk follows
// the light/dark toggle through CSS variables. Slots are style objects, not
// Tailwind classes: Clerk's own emotion styles out-rank utility classes.
const clerkAppearance = {
    variables: {
        colorPrimary: "var(--site-ink)",
        // Clerk derives its borders and dividers from this; its default (black) vanishes on dark.
        colorNeutral: "var(--site-ink)",
        colorBackground: "var(--site-paper)",
        colorText: "var(--site-ink)",
        colorTextSecondary: "var(--site-muted)",
        colorInputBackground: "var(--site-paper)",
        colorInputText: "var(--site-ink)",
        borderRadius: "4px",
        fontFamily: "'DM Sans', sans-serif",
    },
    elements: {
        // Clerk gives cardBox a fixed 25rem width, shadow and border; flatten it
        // so the form fills our card instead of sitting offset inside it.
        rootBox: { width: "100%" },
        cardBox: { width: "100%", boxShadow: "none", border: 0, background: "transparent" },
        // 2px side padding: Clerk's inner wrappers clip overflow, which shaved the
        // outer 1px border off the first and last controls in each row.
        card: { width: "100%", boxShadow: "none", border: 0, background: "transparent", padding: "0 2px", gap: 24 },
        headerTitle: { fontFamily: "'Newsreader', Georgia, serif", fontWeight: 400, fontSize: 28, lineHeight: 1.17 },
        headerSubtitle: { fontSize: 14, color: "var(--site-muted)" },
        socialButtonsBlockButton: {
            minHeight: 44,
            border: "1px solid color-mix(in srgb, var(--site-ink) 24%, transparent)",
            boxShadow: "none",
            background: "var(--site-paper)",
            backgroundImage: "none",
            "&:hover": { background: "var(--site-wash)" },
        },
        socialButtonsBlockButtonText: { color: "var(--site-ink)" },
        // Provider glyphs such as GitHub's are black; lighten them on the dark theme only.
        providerIcon__github: { ".dark &": { filter: "invert(1)" } },
        dividerLine: { background: "var(--site-line)" },
        dividerText: { color: "var(--site-muted)" },
        formFieldLabel: { color: "var(--site-ink)" },
        formFieldInput: {
            minHeight: 44,
            border: "1px solid color-mix(in srgb, var(--site-ink) 24%, transparent)",
            boxShadow: "none",
            background: "var(--site-paper)",
            color: "var(--site-ink)",
            "&:focus": { borderColor: "var(--site-accent)", boxShadow: "0 0 0 1px var(--site-accent)" },
        },
        // Mirrors .develop-button on the landing page.
        formButtonPrimary: {
            minHeight: 47,
            borderRadius: 4,
            background: "var(--site-ink)",
            backgroundImage: "none",
            color: "var(--site-bg)",
            boxShadow: "none",
            textTransform: "none",
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 12,
            fontWeight: 400,
            // Clerk paints a gradient sheen over the button with a pseudo-element.
            "&::after, &::before": { display: "none" },
            "&:hover, &:focus": { background: "var(--site-accent)", color: "var(--site-paper)" },
        },
        footerActionLink: { color: "var(--site-accent)" },
        footer: { display: "none" },
    },
};

export default function AuthPage({ mode = "sign-in" }) {
    const isSignUp = mode === "sign-up";
    useSitePage(isSignUp ? "Sign up — SourceMind" : "Sign in — SourceMind");

    return (
        <div className="source-site" data-testid="auth-page">
            <SiteHeader />
            <main id="main-content" className="site-container" style={{ maxWidth: 480, padding: "64px 0 96px" }}>
                <p className="eyebrow" style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--site-muted)", marginBottom: 21 }}>
                    <span className="tiny-square" aria-hidden="true" />
                    SourceMind / {isSignUp ? "Sign up" : "Sign in"}
                </p>

                <div
                    data-testid="clerk-auth-card"
                    style={{ background: "var(--site-paper)", border: "1px solid var(--site-line)", borderRadius: 4, padding: 32 }}
                >
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

                    <div style={{ marginTop: 24, textAlign: "center", fontSize: 13, color: "var(--site-muted)" }}>
                        {isSignUp ? (
                            <>
                                Already have an account?{" "}
                                <Link to="/sign-in" style={{ color: "var(--site-accent)" }} data-testid="switch-to-signin">
                                    Sign in
                                </Link>
                            </>
                        ) : (
                            <>
                                Don't have an account?{" "}
                                <Link to="/sign-up" style={{ color: "var(--site-accent)" }} data-testid="switch-to-signup">
                                    Sign up
                                </Link>
                            </>
                        )}
                    </div>
                </div>

                <p className="eyebrow" style={{ textAlign: "center", color: "var(--site-muted)", marginTop: 24 }}>
                    Protected by Clerk
                </p>
            </main>
            <SiteFooter />
        </div>
    );
}
