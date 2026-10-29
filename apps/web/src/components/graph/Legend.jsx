import React from "react";
import "./graph.css";
import { EDGE_TYPES, presentTypes } from "./graphModel";

function Swatch({ type }) {
    return (
        <svg className="gv-legend-swatch" viewBox="0 0 36 10" aria-hidden="true" focusable="false">
            <line x1="2" y1="5" x2="34" y2="5" className={`gv-edge gv-edge-${type}`} />
        </svg>
    );
}

function HexKey({ className }) {
    return (
        <svg width="18" height="18" viewBox="-12 -12 24 24" aria-hidden="true" focusable="false">
            <g className={className}>
                <polygon
                    className="gv-hex"
                    points="0,-9 7.8,-4.5 7.8,4.5 0,9 -7.8,4.5 -7.8,-4.5"
                />
            </g>
        </svg>
    );
}

/**
 * Says what the shapes and lines mean. Lists only the relation types that are actually drawn, so
 * the legend never explains a line that is not on the screen. The wording for updates, extends and
 * derives follows Supermemory's own.
 */
export default function Legend({ edges, kind = "memory", testId = "graph-legend" }) {
    const types = kind === "memory" ? presentTypes(edges) : [];
    return (
        <div data-testid={testId} className="grid gap-5 sm:grid-cols-2 text-[12px] text-sm-text-secondary">
            <div>
                <p className="font-mono text-[10px] uppercase tracking-wider mb-2.5">
                    {kind === "memory" ? "Relationships" : "Lines"}
                </p>
                {kind === "memory" ? (
                    types.length === 0 ? (
                        <p>No relationships in view.</p>
                    ) : (
                        <ul className="space-y-2">
                            {types.map((type) => (
                                <li key={type} className="flex items-start gap-3" data-testid={`legend-${type}`}>
                                    <Swatch type={type} />
                                    <span>
                                        <span className="text-sm-text font-medium">{EDGE_TYPES[type].label}</span>
                                        {EDGE_TYPES[type].directed ? " (points to the older or source memory)" : ""}
                                        <span className="block">{EDGE_TYPES[type].blurb}</span>
                                    </span>
                                </li>
                            ))}
                        </ul>
                    )
                ) : (
                    <p>
                        A line joins two people who both contributed to the same memories. The thicker the line,
                        the more memories they share.
                    </p>
                )}
            </div>
            <div>
                <p className="font-mono text-[10px] uppercase tracking-wider mb-2.5">Shapes</p>
                {kind === "memory" ? (
                    <ul className="space-y-2">
                        <li className="flex items-center gap-3">
                            <HexKey className="gv-node-focal" />
                            <span>This memory</span>
                        </li>
                        <li className="flex items-center gap-3">
                            <HexKey className="gv-node-current" />
                            <span>A current memory (hexagon)</span>
                        </li>
                        <li className="flex items-center gap-3">
                            <HexKey className="gv-node-superseded" />
                            <span>A superseded memory (dashed outline)</span>
                        </li>
                    </ul>
                ) : (
                    <p>A rounded rectangle is a contributor, sized the same for everyone; the count underneath is
                        the number of current memories they hold a share of.</p>
                )}
            </div>
        </div>
    );
}
