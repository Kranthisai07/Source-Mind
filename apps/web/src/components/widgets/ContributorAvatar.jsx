import React from "react";
import { initials } from "../../lib/format";

export default function ContributorAvatar({ contributor, size = 28, showTooltip = true }) {
    if (!contributor) return null;
    const name = contributor.name || contributor.login || contributor;
    const login = contributor.login || contributor;
    const color = contributor.avatarColor || "#4F7EFF";
    return (
        <div
            className="rounded-full flex items-center justify-center text-white font-semibold shrink-0 select-none"
            style={{
                width: size, height: size,
                background: color,
                fontSize: size * 0.4,
                boxShadow: `0 0 0 2px #12121A`,
            }}
            title={showTooltip ? `${name} · @${login}` : undefined}
        >
            {initials(name)}
        </div>
    );
}

export function ContributorStack({ contributors = [], max = 4, size = 24 }) {
    const shown = contributors.slice(0, max);
    const more = contributors.length - shown.length;
    return (
        <div className="flex items-center">
            {shown.map((c, i) => (
                <div key={i} style={{ marginLeft: i === 0 ? 0 : -8 }}>
                    <ContributorAvatar contributor={c} size={size} />
                </div>
            ))}
            {more > 0 && (
                <div
                    className="rounded-full flex items-center justify-center text-sm-text-secondary bg-sm-surface border border-sm-border text-[10px] font-mono"
                    style={{ width: size, height: size, marginLeft: -8 }}
                >
                    +{more}
                </div>
            )}
        </div>
    );
}
