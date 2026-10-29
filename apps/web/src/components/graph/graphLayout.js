// Deterministic layouts for the graph views. Pure functions: no React, no colours, no randomness,
// so the same data always gives the same picture and the tests can pin it.
//
// Two shapes, both capped by the API (50 related memories, 200 contributors):
//   radialLayout    a memory and its neighbours on rings by hop distance
//   circularLayout  contributors on a circle, busiest first

export const MEMORY_RADIUS = 24;
export const FOCAL_RADIUS = 28;
export const PERSON_HALF_WIDTH = 50;
export const PERSON_HALF_HEIGHT = 18;

const TAU = Math.PI * 2;
const round = (n) => Math.round(n * 100) / 100;
const byId = (a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

const ASPECT = 150 / 210; // the picture is wider than tall
const MEMORY_PITCH = 76; // centre-to-centre room a hexagon and its label need along a ring
const PERSON_PITCH = 112;
const LANE_STEP = 78; // distance between the concentric lanes a crowded ring is spread over

/** Ramanujan's approximation of an ellipse's perimeter. */
const perimeter = (a, b) => Math.PI * (3 * (a + b) - Math.sqrt((3 * a + b) * (a + 3 * b)));

/**
 * Spread `items` over concentric lanes starting at radius `r0`, as many per lane as fit without
 * touching. Returns the placed offsets from the centre and the outermost radius used.
 */
function packLanes(items, r0, { pitch, startAngle, place, laneStep = LANE_STEP }) {
    let r = r0;
    let index = 0;
    let lane = 0;
    let outer = r0;
    while (index < items.length) {
        const capacity = Math.max(1, Math.floor(perimeter(r, r * ASPECT) / pitch));
        const group = items.slice(index, index + capacity);
        const start = startAngle + lane * 0.2;
        group.forEach((item, i) => {
            const angle = start + (TAU * i) / group.length;
            place(item, r * Math.cos(angle), r * ASPECT * Math.sin(angle));
        });
        outer = r;
        index += group.length;
        r += laneStep;
        lane += 1;
    }
    return outer;
}

const finish = (offsets, outer, { width, height, margin }) => {
    // The canvas grows when crowded rings need more room than the default, never shrinks.
    const w = Math.max(width, Math.ceil(2 * (outer + margin)));
    const h = Math.max(height, Math.ceil(2 * (outer * ASPECT + margin)));
    const positions = {};
    for (const [id, o] of Object.entries(offsets)) {
        positions[id] = { x: round(w / 2 + o.x), y: round(h / 2 + o.y) };
    }
    return { positions, width: w, height: h };
};

/**
 * Memory at the centre, everything else on rings by hop count, each ring a little rotated. A ring
 * with more nodes than fit is spread over extra lanes, and the canvas grows to hold them.
 * Returns { positions, width, height }.
 */
export function radialLayout(nodes, { width = 720, height = 440, margin = 68 } = {}) {
    if (!nodes.length) return { positions: {}, width, height };
    const centre = nodes.find((n) => n.focal) || nodes[0];
    const offsets = { [centre.id]: { x: 0, y: 0 } };

    const rest = nodes.filter((n) => n.id !== centre.id);
    const rings = new Map();
    for (const n of rest) {
        const hop = Math.max(1, n.hops || 1);
        if (!rings.has(hop)) rings.set(hop, []);
        rings.get(hop).push(n);
    }
    const maxHops = Math.max(1, ...rings.keys());
    const gap = Math.min(210, Math.max(115, (width / 2 - margin) / maxHops)); // rings never closer than 115, the canvas grows instead

    let outer = 0;
    for (const hop of [...rings.keys()].sort((a, b) => a - b)) {
        const ring = rings.get(hop).sort(byId);
        // Never closer to the previous ring than one lane, however many nodes it took.
        const radius = Math.max(gap * hop, outer + (outer ? LANE_STEP : 0));
        outer = packLanes(ring, radius, {
            pitch: MEMORY_PITCH,
            // A different start angle per ring, so spokes do not line up into one long diameter.
            startAngle: -Math.PI / 2 + (hop - 1) * 0.45,
            place: (n, x, y) => {
                offsets[n.id] = { x, y };
            },
        });
    }
    return finish(offsets, outer, { width, height, margin });
}

/**
 * Busiest contributor first, evenly around an ellipse; a lone contributor sits in the middle.
 * More contributors than fit on one ring continue on outer lanes. Returns { positions, width, height }.
 */
export function circularLayout(nodes, { width = 720, height = 440, margin = 76 } = {}) {
    const ordered = [...nodes].sort(
        (a, b) => (b.memoryCount || 0) - (a.memoryCount || 0) || byId(a, b)
    );
    if (ordered.length === 0) return { positions: {}, width, height };
    if (ordered.length === 1) {
        return { positions: { [ordered[0].id]: { x: width / 2, y: height / 2 } }, width, height };
    }
    const offsets = {};
    const outer = packLanes(ordered, width / 2 - margin, {
        pitch: PERSON_PITCH,
        laneStep: PERSON_PITCH, // a tile is 100px wide, so lanes must be further apart than hexagon lanes
        startAngle: -Math.PI / 2,
        place: (n, x, y) => {
            offsets[n.id] = { x, y };
        },
    });
    return finish(offsets, outer, { width, height, margin });
}

/** Points of a pointy-top hexagon, ready for an SVG polygon. */
export function hexPoints(x, y, radius) {
    return Array.from({ length: 6 }, (_, k) => {
        const angle = Math.PI / 6 + (Math.PI / 3) * k; // vertices at 30, 90, ...: pointy top
        return `${round(x + radius * Math.cos(angle))},${round(y + radius * Math.sin(angle))}`;
    }).join(" ");
}

export function nodeShape(node) {
    return node.kind === "person"
        ? { type: "rect", halfWidth: PERSON_HALF_WIDTH, halfHeight: PERSON_HALF_HEIGHT }
        : { type: "hex", radius: node.focal ? FOCAL_RADIUS : MEMORY_RADIUS };
}

/** Where the segment from `from` towards `to` leaves the node's outline. */
function leave(node, from, to) {
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const length = Math.hypot(dx, dy) || 1;
    const shape = nodeShape(node);
    let t;
    if (shape.type === "hex") {
        t = shape.radius + 2;
    } else {
        const tx = dx === 0 ? Infinity : shape.halfWidth / Math.abs(dx / length);
        const ty = dy === 0 ? Infinity : shape.halfHeight / Math.abs(dy / length);
        t = Math.min(tx, ty) + 2;
    }
    return { x: round(from.x + (dx / length) * t), y: round(from.y + (dy / length) * t) };
}

/** A line between two nodes that stops at their outlines instead of running into their centres. */
export function edgeLine(a, pa, b, pb) {
    const start = leave(a, pa, pb);
    const end = leave(b, pb, pa);
    return { x1: start.x, y1: start.y, x2: end.x, y2: end.y };
}

/** A small triangle at (x2, y2) pointing along the line, for edges that have a direction. */
export function arrowHead({ x1, y1, x2, y2 }, size = 7) {
    const angle = Math.atan2(y2 - y1, x2 - x1);
    const spread = Math.PI / 7;
    const left = [x2 - size * Math.cos(angle - spread), y2 - size * Math.sin(angle - spread)];
    const right = [x2 - size * Math.cos(angle + spread), y2 - size * Math.sin(angle + spread)];
    return `${round(x2)},${round(y2)} ${round(left[0])},${round(left[1])} ${round(right[0])},${round(right[1])}`;
}

export function midpoint({ x1, y1, x2, y2 }) {
    return { x: round((x1 + x2) / 2), y: round((y1 + y2) / 2) };
}
