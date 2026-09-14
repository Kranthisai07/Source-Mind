import React from "react";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, Lock, EyeOff, WifiOff, Timer, RefreshCw } from "lucide-react";
import { Button } from "../ui/button";

/**
 * A failed request, rendered so it cannot be mistaken for an empty one.
 *
 * Shares EmptyState's vertical rhythm on purpose — same centred icon, same
 * headline size, same single grey sentence — so the layout is familiar while
 * the meaning is unambiguous. §5's template is for "nothing here yet". This is
 * for "we could not find out", and the two must never look alike.
 *
 * §2 colour discipline holds: the icon carries the only colour, and only where
 * it is semantic — danger for a hard failure, warning for a throttle. A 404 is
 * deliberately neutral grey, because a coloured alarm would itself hint that
 * something was being withheld.
 */

const PRESENTATION = {
    auth:           { icon: Lock,          tone: "text-content-secondary" },
    forbidden:      { icon: EyeOff,        tone: "text-warning" },
    missing:        { icon: EyeOff,        tone: "text-content-muted" },
    "rate-limited": { icon: Timer,         tone: "text-warning" },
    server:         { icon: AlertTriangle, tone: "text-danger" },
    network:        { icon: WifiOff,       tone: "text-danger" },
    unknown:        { icon: AlertTriangle, tone: "text-danger" },
};

export default function ErrorState({ error, onRetry, testId = "error-state" }) {
    const navigate = useNavigate();
    if (!error) return null;

    const { icon: Icon, tone } = PRESENTATION[error.kind] ?? PRESENTATION.unknown;

    return (
        <div
            data-testid={testId}
            data-error-kind={error.kind}
            role="alert"
            className="flex flex-col items-center justify-center text-center px-6 py-16"
        >
            <Icon className={`w-10 h-10 mb-4 ${tone}`} strokeWidth={1.5} aria-hidden="true" />

            <h3 className="text-[16px] font-semibold text-content">{error.title}</h3>

            <p className="text-body text-content-secondary mt-2 max-w-[420px] leading-relaxed">
                {error.detail}
            </p>

            {/* The status code helps a bug report without meaning anything to a
                reader who does not need it. Never shown for 404, where even the
                code is a signal. */}
            {error.status && error.kind !== "missing" && (
                <p className="font-mono text-[10.5px] text-content-muted mt-2">
                    {error.status}{error.code ? ` · ${error.code}` : ""}
                </p>
            )}

            <div className="mt-6 flex items-center gap-2">
                {/* Reuses the existing route RequireAuth already redirects to,
                    rather than introducing a second sign-in path. */}
                {error.kind === "auth" && (
                    <Button
                        data-testid="error-reauth"
                        onClick={() => navigate("/sign-in", { replace: true })}
                        className="h-9 bg-brand-fill hover:bg-brand-fill-hover text-white"
                    >
                        Sign in again
                    </Button>
                )}

                {error.retryable && onRetry && (
                    <Button
                        data-testid="error-retry"
                        variant="outline"
                        onClick={onRetry}
                        className="h-9 bg-surface border-hairline text-content"
                    >
                        <RefreshCw className="w-3.5 h-3.5" aria-hidden="true" /> Try again
                    </Button>
                )}
            </div>
        </div>
    );
}
