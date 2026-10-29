import React, { useMemo, useRef, useState } from "react";
import "./graph.css";
import {
    arrowHead,
    circularLayout,
    edgeLine,
    hexPoints,
    midpoint,
    nodeShape,
    PERSON_HALF_HEIGHT,
    PERSON_HALF_WIDTH,
    radialLayout,
} from "./graphLayout";
import { describeNode, EDGE_TYPES, edgeLabel, neighbourhood, sharedWidth } from "./graphModel";

/**
 * The node-link picture: hexagons are memories, rounded rectangles are people, the one blue
 * hexagon is the memory being looked at. Hover, focus and selection light a node's neighbourhood
 * and dim the rest; only those edges carry a label, so a dense graph is never a wall of text.
 *
 * Every node is a focusable button (Enter or Space selects, arrows move between nodes, Escape
 * clears), has a text description for screen readers, and has a 24px-or-larger hit area. The list
 * view in GraphView is the table twin of this picture.
 */
export default function GraphCanvas({
    nodes,
    edges,
    layout = "radial",
    selectedId = null,
    onSelect = () => {},
    label = "Graph",
    testId = "graph-canvas",
}) {
    const [hoverId, setHoverId] = useState(null);
    const [focusId, setFocusId] = useState(null);
    const refs = useRef({});

    const { positions, width, height } = useMemo(
        () => (layout === "circular" ? circularLayout(nodes) : radialLayout(nodes)),
        [nodes, layout]
    );
    const byId = useMemo(() => Object.fromEntries(nodes.map((n) => [n.id, n])), [nodes]);
    const activeId = hoverId || focusId || selectedId;
    const lit = useMemo(() => (activeId ? neighbourhood(edges, activeId) : null), [edges, activeId]);

    const order = nodes.map((n) => n.id);
    const moveFocus = (id, step) => {
        const index = order.indexOf(id);
        const next = order[(index + step + order.length) % order.length];
        refs.current[next]?.focus();
    };
    const onKeyDown = (event, id) => {
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onSelect(id === selectedId ? null : id);
        } else if (event.key === "ArrowRight" || event.key === "ArrowDown") {
            event.preventDefault();
            moveFocus(id, 1);
        } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
            event.preventDefault();
            moveFocus(id, -1);
        } else if (event.key === "Home" || event.key === "End") {
            event.preventDefault();
            refs.current[event.key === "Home" ? order[0] : order[order.length - 1]]?.focus();
        } else if (event.key === "Escape") {
            onSelect(null);
        }
    };

    return (
        <svg
            className="gv-svg"
            viewBox={`0 0 ${width} ${height}`}
            role="group"
            aria-label={label}
            data-testid={testId}
        >
            <g data-testid={`${testId}-edges`}>
                {edges.map((edge) => {
                    const a = byId[edge.source];
                    const b = byId[edge.target];
                    const pa = positions[edge.source];
                    const pb = positions[edge.target];
                    if (!a || !b || !pa || !pb) return null;
                    const line = edgeLine(a, pa, b, pb);
                    const dim = lit && !lit.edgeIds.has(edge.id);
                    const labelled = lit && lit.edgeIds.has(edge.id);
                    const mid = midpoint(line);
                    const text =
                        edge.type === "shared"
                            ? `${edge.weight} shared`
                            : edgeLabel(edge.type);
                    return (
                        <g key={edge.id} className={dim ? "gv-dim" : undefined} data-edge-type={edge.type}>
                            <line
                                {...line}
                                className={`gv-edge gv-edge-${edge.type}`}
                                style={edge.type === "shared" ? { strokeWidth: sharedWidth(edge.weight) } : undefined}
                            />
                            {EDGE_TYPES[edge.type]?.directed && (
                                <polygon points={arrowHead(line)} className={`gv-arrow gv-edge-${edge.type}`} />
                            )}
                            {labelled && (
                                <text x={mid.x} y={mid.y} dy="0.35em" className="gv-edge-label">
                                    {text}
                                </text>
                            )}
                        </g>
                    );
                })}
            </g>

            <g data-testid={`${testId}-nodes`}>
                {nodes.map((node) => {
                    const p = positions[node.id];
                    if (!p) return null;
                    const shape = nodeShape(node);
                    const selected = node.id === selectedId;
                    const focused = node.id === focusId;
                    const dim = lit && !lit.nodeIds.has(node.id);
                    const classes = ["gv-node"];
                    if (node.kind === "memory") {
                        classes.push(node.isCurrent ? "gv-node-current" : "gv-node-superseded");
                        if (node.focal) classes.push("gv-node-focal");
                    }
                    if (dim) classes.push("gv-dim");
                    const description = describeNode(node);
                    return (
                        <g
                            key={node.id}
                            ref={(el) => {
                                refs.current[node.id] = el;
                            }}
                            className={classes.join(" ")}
                            tabIndex={0}
                            role="button"
                            aria-pressed={selected}
                            aria-label={description}
                            data-testid={`graph-node-${node.id}`}
                            data-kind={node.kind}
                            onClick={() => onSelect(selected ? null : node.id)}
                            onKeyDown={(event) => onKeyDown(event, node.id)}
                            onPointerEnter={() => setHoverId(node.id)}
                            onPointerLeave={() => setHoverId(null)}
                            onFocus={() => setFocusId(node.id)}
                            onBlur={() => setFocusId(null)}
                        >
                            <title>{description}</title>
                            {shape.type === "hex" ? (
                                <>
                                    <circle className="gv-hit" cx={p.x} cy={p.y} r={shape.radius + 6} />
                                    {(selected || focused) && (
                                        <polygon className="gv-ring" points={hexPoints(p.x, p.y, shape.radius + 6)} />
                                    )}
                                    <polygon className="gv-hex" points={hexPoints(p.x, p.y, shape.radius)} />
                                    <text className="gv-glyph" x={p.x} y={p.y} dy="0.35em">
                                        {`v${node.version}`}
                                    </text>
                                    <text className="gv-label" x={p.x} y={p.y + shape.radius + 16}>
                                        {node.label}
                                    </text>
                                </>
                            ) : (
                                <>
                                    <rect
                                        className="gv-hit"
                                        x={p.x - PERSON_HALF_WIDTH - 4}
                                        y={p.y - PERSON_HALF_HEIGHT - 4}
                                        width={(PERSON_HALF_WIDTH + 4) * 2}
                                        height={(PERSON_HALF_HEIGHT + 4) * 2}
                                    />
                                    {(selected || focused) && (
                                        <rect
                                            className="gv-ring"
                                            x={p.x - PERSON_HALF_WIDTH - 4}
                                            y={p.y - PERSON_HALF_HEIGHT - 4}
                                            width={(PERSON_HALF_WIDTH + 4) * 2}
                                            height={(PERSON_HALF_HEIGHT + 4) * 2}
                                            rx={9}
                                        />
                                    )}
                                    <rect
                                        className="gv-person"
                                        x={p.x - PERSON_HALF_WIDTH}
                                        y={p.y - PERSON_HALF_HEIGHT}
                                        width={PERSON_HALF_WIDTH * 2}
                                        height={PERSON_HALF_HEIGHT * 2}
                                        rx={6}
                                    />
                                    <text className="gv-person-name" x={p.x} y={p.y - 3}>
                                        {node.label}
                                    </text>
                                    <text className="gv-sub" x={p.x} y={p.y + 11}>
                                        {`${node.memoryCount} ${node.memoryCount === 1 ? "memory" : "memories"}`}
                                    </text>
                                </>
                            )}
                        </g>
                    );
                })}
            </g>
        </svg>
    );
}
