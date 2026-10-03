import React, { useEffect, useState } from "react";

// Circular SVG gauge (0-100). Color transitions red -> amber -> green.
export default function HealthGauge({ score = 0, size = 160, strokeWidth = 10, label = "Knowledge Health" }) {
    const [animated, setAnimated] = useState(() => Math.max(0, Math.min(100, Math.round(score))));
    useEffect(() => {
        setAnimated(Math.max(0, Math.min(100, Math.round(score))));
    }, [score]);

    const r = (size - strokeWidth) / 2;
    const c = 2 * Math.PI * r;
    const display = Math.max(0, Math.min(100, animated));
    const pct = display / 100;
    const color =
        display < 50 ? "#EF4444" :
        display < 75 ? "#F59E0B" :
        "#34D399";
    const rot = `rotate(-90 ${size / 2} ${size / 2})`;

    return (
        <div className="flex flex-col items-center" data-testid="health-gauge">
            <svg width={size} height={size} className="drop-shadow-[0_0_24px_rgba(79,126,255,0.08)]">
                <circle
                    cx={size / 2} cy={size / 2} r={r}
                    fill="none" stroke="#1E1E2E" strokeWidth={strokeWidth}
                />
                <circle
                    cx={size / 2} cy={size / 2} r={r}
                    fill="none" stroke={color} strokeWidth={strokeWidth}
                    strokeDasharray={c}
                    strokeDashoffset={c * (1 - pct)}
                    strokeLinecap="round"
                    transform={rot}
                    style={{ transition: "stroke 300ms ease" }}
                />
                <text
                    x="50%" y="50%" dy="0.18em"
                    textAnchor="middle"
                    className="font-mono"
                    style={{ fill: "#E8E8F0", fontSize: size * 0.28, fontWeight: 600 }}
                >
                    {display}
                </text>
                <text
                    x="50%" y={size / 2 + size * 0.19}
                    textAnchor="middle"
                    style={{ fill: "#8888A8", fontSize: size * 0.085, letterSpacing: "0.08em" }}
                >
                    / 100
                </text>
            </svg>
            {label && (
                <div className="mt-2 text-[11px] uppercase tracking-[0.14em] text-sm-text-secondary">{label}</div>
            )}
        </div>
    );
}
