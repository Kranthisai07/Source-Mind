/**
 * Publishing the signed-in user id to plain modules.
 *
 * THE DEFECT
 *
 * ClerkTokenBridge's identity effect opened with:
 *
 *     if (previousUserId.current === undefined) {
 *         previousUserId.current = userId ?? null;
 *         return;                       // <- before setCurrentUserId
 *     }
 *     setCurrentUserId(userId ?? null);
 *
 * so on the FIRST settled render it recorded the id and returned without
 * publishing it. Only a later change ever reached `setCurrentUserId`. The
 * comment beside that call says it publishes "on every settled render, not
 * only on change", and warns that otherwise "a submission key minted before
 * this ran would carry a null user for the life of the panel" — which is
 * precisely what happened.
 *
 * The ordinary case is a user who signs in and never switches account, so the
 * null was the normal state rather than an edge case. Sign-out reloads the
 * document, which is why this never produced cross-user key reuse; the
 * user dimension of every key was simply inert.
 *
 * These assert the identity that is actually published, not the absence of a
 * warning.
 */

import React from "react";
import { render } from "@testing-library/react";

const mockUseAuth = jest.fn();
const mockResetApiCaches = jest.fn();

jest.mock("@clerk/clerk-react", () => ({
    __esModule: true,
    useAuth: (...a) => mockUseAuth(...a),
}));

jest.mock("@/lib/api", () => ({
    __esModule: true,
    useMocks: false,
    resetApiCaches: (...a) => mockResetApiCaches(...a),
    default: {},
}));

import { ClerkTokenBridge } from "./ClerkAuthBridge";
import { getCurrentUserId, setCurrentUserId } from "@/lib/currentUser";
import { submissionIdentity } from "@/lib/submissionKey";

const signedInAs = (userId) => ({
    isLoaded: true,
    isSignedIn: userId != null,
    userId,
    getToken: async () => (userId ? "token" : null),
});

beforeEach(() => {
    setCurrentUserId(null);
    mockUseAuth.mockReset();
    mockResetApiCaches.mockReset();
});

afterEach(() => {
    setCurrentUserId(null);
});

describe("the initial settled sign-in", () => {
    test("publishes the signed-in user, with no later change needed", () => {
        mockUseAuth.mockReturnValue(signedInAs("user_alpha"));

        render(<ClerkTokenBridge />);

        expect(getCurrentUserId()).toBe("user_alpha");
    });

    test("publishes through the real loading -> settled sequence", () => {
        // What an actual mount looks like: Clerk reports isLoaded=false first.
        mockUseAuth.mockReturnValue({
            isLoaded: false, isSignedIn: false, userId: null, getToken: async () => null,
        });
        const { rerender } = render(<ClerkTokenBridge />);
        expect(getCurrentUserId()).toBeNull();      // nothing to publish yet

        mockUseAuth.mockReturnValue(signedInAs("user_alpha"));
        rerender(<ClerkTokenBridge />);

        expect(getCurrentUserId()).toBe("user_alpha");
    });

    test("a submission key minted right after mount carries the real user", () => {
        mockUseAuth.mockReturnValue(signedInAs("user_alpha"));
        render(<ClerkTokenBridge />);

        const identity = submissionIdentity({
            payload: { content: "a decision", tags: [], category: "general" },
            workspaceId: "11111111-1111-4111-8111-111111111111",
            userId: getCurrentUserId(),
        });

        // The whole point of the seam: the key's user dimension is real.
        expect(JSON.parse(identity).userId).toBe("user_alpha");
    });

    test("signed out settles as null rather than staying unknown", () => {
        mockUseAuth.mockReturnValue(signedInAs(null));
        render(<ClerkTokenBridge />);
        expect(getCurrentUserId()).toBeNull();
    });
});

describe("the protections that already existed still hold", () => {
    test("a later A -> B change publishes B and resets identity-scoped caches", () => {
        mockUseAuth.mockReturnValue(signedInAs("user_A"));
        const { rerender } = render(<ClerkTokenBridge />);
        expect(getCurrentUserId()).toBe("user_A");

        mockUseAuth.mockReturnValue(signedInAs("user_B"));
        rerender(<ClerkTokenBridge />);

        expect(getCurrentUserId()).toBe("user_B");
        // Publishing earlier must not have consumed the change detection that
        // clears A's cached workspace before B reads it.
        expect(mockResetApiCaches).toHaveBeenCalledTimes(1);
    });

    test("re-rendering with an UNCHANGED user does not reset caches", () => {
        mockUseAuth.mockReturnValue(signedInAs("user_A"));
        const { rerender } = render(<ClerkTokenBridge />);
        rerender(<ClerkTokenBridge />);
        rerender(<ClerkTokenBridge />);

        expect(getCurrentUserId()).toBe("user_A");
        // A reset on every render would throw away the workspace cache
        // constantly; only a real change may do that.
        expect(mockResetApiCaches).not.toHaveBeenCalled();
    });

    test("sign-out publishes null and resets caches once", () => {
        mockUseAuth.mockReturnValue(signedInAs("user_A"));
        const { rerender } = render(<ClerkTokenBridge />);

        mockUseAuth.mockReturnValue(signedInAs(null));
        rerender(<ClerkTokenBridge />);

        expect(getCurrentUserId()).toBeNull();
        expect(mockResetApiCaches).toHaveBeenCalledTimes(1);
    });
});
