import React from "react";

const STEPS = ["Create", "Team"];

/** "Step 1 of 2" bar for the create-workspace flow (name, then who is on the team). */
export default function StepIndicator({ step }) {
    return (
        <div data-testid="step-indicator" aria-label={`Step ${step} of ${STEPS.length}: ${STEPS[step - 1]}`} className="flex items-center gap-3">
            <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-sm-text-muted">Step {step} of {STEPS.length}</span>
            <div className="flex flex-1 gap-1.5" aria-hidden="true">
                {STEPS.map((name, i) => (
                    <div key={name} className="h-1 flex-1 rounded-full" style={{ background: i < step ? "var(--sm-text)" : "var(--sm-border)" }} />
                ))}
            </div>
        </div>
    );
}
