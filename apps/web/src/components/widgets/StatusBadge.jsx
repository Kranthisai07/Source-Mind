import React from "react";
import { statusColor } from "../../lib/format";

export default function StatusBadge({ status, pulse = false, label }) {
    const color = statusColor(status);
    const text = (label || status || "").toString().replace("_", " ").toUpperCase();
    return (
        <span
            data-testid={`status-badge-${status}`}
            className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10.5px] font-semibold font-mono tracking-wider"
            style={{ background: `${color}16`, color, border: `1px solid ${color}33` }}
        >
            <span
                className={`w-1.5 h-1.5 rounded-full relative ${pulse ? "pulse-dot" : ""}`}
                style={{ background: color }}
            />
            {text}
        </span>
    );
}
