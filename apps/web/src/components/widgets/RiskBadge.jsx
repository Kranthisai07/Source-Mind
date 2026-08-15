import React from "react";
import { riskColor } from "../../lib/format";

export default function RiskBadge({ level }) {
    const c = riskColor(level);
    return (
        <span
            data-testid={`risk-badge-${(level||'').toLowerCase()}`}
            className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10.5px] font-semibold font-mono tracking-wider"
            style={{ background: c.bg, color: c.text, border: `1px solid ${c.border}` }}
        >
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: c.text }} />
            {level}
        </span>
    );
}
