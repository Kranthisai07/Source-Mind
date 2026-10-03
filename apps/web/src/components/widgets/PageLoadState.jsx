import React from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "../ui/button";

export function PageLoadError({ error, onRetry, testId = "page-load-error", compact = false }) {
    return (
        <div
            data-testid={testId}
            role="alert"
            className={`sm-card ${compact ? "p-4" : "p-6"} border-sm-red/30`}
        >
            <div className="flex items-start gap-3">
                <AlertTriangle className="w-5 h-5 text-sm-red shrink-0 mt-0.5" />
                <div className="min-w-0 flex-1">
                    <h2 className="text-[14px] font-semibold text-sm-text">
                        {error?.title || "Something went wrong"}
                    </h2>
                    <p className="mt-1 text-[12.5px] text-sm-text-secondary">
                        {error?.detail || "This data could not be loaded."}
                    </p>
                    {error?.retryable && onRetry && (
                        <Button
                            type="button"
                            data-testid={`${testId}-retry`}
                            onClick={onRetry}
                            size="sm"
                            className="mt-4 bg-sm-blue hover:bg-sm-blue/90 text-white"
                        >
                            <RefreshCw className="w-3.5 h-3.5" /> Retry
                        </Button>
                    )}
                </div>
            </div>
        </div>
    );
}

export function PageLoading({ label = "Loading", testId = "page-loading" }) {
    return (
        <div
            data-testid={testId}
            role="status"
            aria-busy="true"
            className="grid grid-cols-1 lg:grid-cols-2 gap-4"
        >
            <div className="sm-card h-[220px] shimmer" />
            <div className="sm-card h-[220px] shimmer" />
            <span className="sr-only">{label}</span>
        </div>
    );
}
