import React from "react";

export default function MetricCard({ icon: Icon, label, value, delta, accent = "#4F7EFF", testId }) {
    return (
        <div
            data-testid={testId}
            className="sm-card p-5 flex flex-col gap-3 fade-in-up relative overflow-hidden"
        >
            <div
                className="absolute -top-10 -right-10 w-32 h-32 rounded-full opacity-10 blur-2xl pointer-events-none"
                style={{ background: accent }}
            />
            <div className="flex items-center justify-between">
                <span className="text-[11px] uppercase tracking-[0.14em] text-sm-text-secondary font-medium">
                    {label}
                </span>
                {Icon && (
                    <div
                        className="w-8 h-8 rounded-lg flex items-center justify-center"
                        style={{ background: `${accent}18`, color: accent }}
                    >
                        <Icon className="w-4 h-4" strokeWidth={2} />
                    </div>
                )}
            </div>
            <div className="font-mono text-[34px] leading-none font-semibold tracking-tight text-sm-text">
                {value}
            </div>
            {delta && (
                <div className="text-[11.5px] text-sm-text-secondary flex items-center gap-1.5">
                    {delta}
                </div>
            )}
        </div>
    );
}
