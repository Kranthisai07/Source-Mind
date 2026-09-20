/**
 * Route + auth smoke check for the marketing integration.
 *
 * WHY THIS EXISTS
 *
 * The generated landing repository this content came from shipped an App.js
 * that replaced the real gate with a pass-through:
 *
 *     const RequireAuth = ({ children }) => children;
 *
 * and dropped <ClerkTokenBridge /> entirely. Merged as-is that would have made
 * every console route public and stopped the API client ever receiving a Clerk
 * token — silently, with a green build, because nothing asserted otherwise.
 *
 * So these assert the three things the integration had to preserve:
 *   1. the public marketing routes render,
 *   2. an unauthenticated visitor is still redirected away from the console,
 *   3. the token bridge is still mounted.
 *
 * App renders its own BrowserRouter, so the route under test is set through
 * window.history rather than by wrapping in MemoryRouter.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";

// Clerk is mocked rather than provided: these assert this app's gating, not
// Clerk's session machinery. `mock`-prefixed so the jest.mock factory may
// close over them.
const mockUseAuth = jest.fn();
const mockSetTokenGetter = jest.fn();

jest.mock("@clerk/clerk-react", () => ({
    __esModule: true,
    useAuth: (...a) => mockUseAuth(...a),
    // The console shell (Sidebar) reads these once the gate lets it mount.
    useUser: () => ({ isSignedIn: true, user: { fullName: "Test User", primaryEmailAddress: { emailAddress: "t@example.com" } } }),
    useClerk: () => ({ signOut: jest.fn() }),
    // AuthPage renders these; the redirect lands on it, so without stubs the
    // assertion fails on Clerk's own widget rather than on the gate.
    SignIn: () => <div data-testid="clerk-sign-in" />,
    SignUp: () => <div data-testid="clerk-sign-up" />,
    ClerkProvider: ({ children }) => children,
}));

jest.mock("@/lib/authToken", () => ({
    __esModule: true,
    setTokenGetter: (...a) => mockSetTokenGetter(...a),
    getToken: async () => null,
}));

// react-markdown and remark-gfm ship ESM only, and CRA's Jest transform does
// not process them — importing App reaches them through MemoryDetail and the
// suite dies on `Unexpected token 'export'`. Stubbed here rather than widening
// transformIgnorePatterns, which would mean editing craco.config.js.
jest.mock("react-markdown", () => ({
    __esModule: true,
    default: ({ children }) => children ?? null,
}));
jest.mock("remark-gfm", () => ({ __esModule: true, default: () => {} }));

// Real gating is only exercised when mock mode is OFF — in mock mode
// RequireAuth returns children by design.
jest.mock("@/lib/api", () => ({
    __esModule: true,
    useMocks: false,
    resetApiCaches: jest.fn(),
    default: {
        searchMemories: jest.fn().mockResolvedValue({ results: [], total_found: 0, latency_ms: 1 }),
    },
}));

import { MemoryRouter } from "react-router-dom";

import App from "./App";
import { RequireAuth } from "@/components/auth/ClerkAuthBridge";

const goTo = (path) => window.history.pushState({}, "", path);

// jsdom does not implement scrollTo; useSitePage calls it on every route.
beforeAll(() => {
    window.scrollTo = () => {};
});

beforeEach(() => {
    mockUseAuth.mockReset();
    mockSetTokenGetter.mockReset();
    mockUseAuth.mockReturnValue({
        isLoaded: true,
        isSignedIn: false,
        userId: null,
        getToken: async () => null,
    });
});

afterEach(() => {
    goTo("/");
});

describe("public marketing routes", () => {
    test("/ renders the landing page", async () => {
        goTo("/");
        render(<App />);
        await waitFor(() => expect(screen.getByTestId("landing-page")).toBeTruthy());
        expect(screen.getByTestId("site-header")).toBeTruthy();
    });

    test("/develop renders without a session", async () => {
        goTo("/develop");
        render(<App />);
        // Reached at all: the route exists and is outside RequireAuth.
        await waitFor(() => expect(screen.getByTestId("site-header")).toBeTruthy());
        expect(screen.queryByTestId("landing-page")).toBeNull();
    });

    test("the landing page still offers a way into the Clerk flow", async () => {
        // The generated chrome had no sign-in entry point. Losing it would
        // strand the sign-in route behind a URL nobody can discover.
        goTo("/");
        render(<App />);
        await waitFor(() => expect(screen.getByTestId("landing-page")).toBeTruthy());
        const signin = screen.getByTestId("landing-signin");
        expect(signin.getAttribute("href")).toContain("/sign-in");
    });
});

describe("the console is still gated", () => {
    test("an unauthenticated visitor to /dashboard is redirected to /sign-in", async () => {
        goTo("/dashboard");
        render(<App />);
        await waitFor(() =>
            expect(window.location.pathname).toMatch(/\/sign-in$/)
        );
    });

    test("a signed-in visitor is let through", async () => {
        // Asserted on the gate itself rather than by mounting /dashboard: the
        // real Dashboard would pull in the whole API surface, and a stubbed
        // page that renders is not evidence about RequireAuth anyway. This is
        // the exact property — signed in, children render, no <Navigate>.
        mockUseAuth.mockReturnValue({
            isLoaded: true,
            isSignedIn: true,
            userId: "user_live",
            getToken: async () => "t",
        });
        render(
            <MemoryRouter initialEntries={["/dashboard"]}>
                <RequireAuth><div data-testid="console-shell" /></RequireAuth>
            </MemoryRouter>
        );
        expect(screen.getByTestId("console-shell")).toBeTruthy();
    });

    test("the session is still resolving: no redirect, no render", async () => {
        mockUseAuth.mockReturnValue({
            isLoaded: false,
            isSignedIn: false,
            userId: null,
            getToken: async () => null,
        });
        goTo("/dashboard");
        render(<App />);
        await waitFor(() => expect(screen.getByText(/Loading session/i)).toBeTruthy());
        expect(window.location.pathname).toMatch(/\/dashboard$/);
    });
});

describe("the token bridge is still mounted", () => {
    test("ClerkTokenBridge publishes getToken to the API client", async () => {
        goTo("/");
        render(<App />);
        await waitFor(() => expect(mockSetTokenGetter).toHaveBeenCalled());
    });
});
