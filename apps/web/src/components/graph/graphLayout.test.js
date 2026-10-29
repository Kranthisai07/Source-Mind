import {
    arrowHead,
    circularLayout,
    edgeLine,
    hexPoints,
    MEMORY_RADIUS,
    midpoint,
    PERSON_HALF_HEIGHT,
    PERSON_HALF_WIDTH,
    radialLayout,
} from "./graphLayout";

const memoryRing = (count, hops = 1) => [
    { id: "focus", focal: true, hops: 0 },
    ...Array.from({ length: count }, (_, i) => ({ id: `n${String(i).padStart(2, "0")}`, hops })),
];

const closest = (positions) => {
    const pts = Object.values(positions);
    let min = Infinity;
    for (let a = 0; a < pts.length; a += 1) {
        for (let b = a + 1; b < pts.length; b += 1) {
            min = Math.min(min, Math.hypot(pts[a].x - pts[b].x, pts[a].y - pts[b].y));
        }
    }
    return min;
};

describe("radialLayout", () => {
    test("empty and single-node inputs do not throw", () => {
        expect(radialLayout([])).toEqual({ positions: {}, width: 720, height: 440 });
        const one = radialLayout([{ id: "a", focal: true, hops: 0 }]);
        expect(one.positions.a).toEqual({ x: 360, y: 220 });
    });

    test("the focal memory is at the centre and farther hops are farther out", () => {
        const nodes = [
            { id: "focus", focal: true, hops: 0 },
            { id: "a", hops: 1 },
            { id: "b", hops: 2 },
            { id: "c", hops: 3 },
        ];
        const { positions, width, height } = radialLayout(nodes);
        const distance = (id) => Math.hypot(positions[id].x - width / 2, positions[id].y - height / 2);
        expect(positions.focus).toEqual({ x: width / 2, y: height / 2 });
        expect(distance("a")).toBeLessThan(distance("b"));
        expect(distance("b")).toBeLessThan(distance("c"));
    });

    test("the picture does not depend on the order the nodes arrive in", () => {
        const nodes = memoryRing(9);
        const shuffled = [...nodes].reverse();
        expect(radialLayout(shuffled).positions).toEqual(radialLayout(nodes).positions);
    });

    test("small graphs keep the compact default canvas", () => {
        const { width, height } = radialLayout(memoryRing(8));
        expect([width, height]).toEqual([720, 440]);
    });

    test.each([4, 8, 16, 24, 40, 49])(
        "a ring of %i neighbours never lets two hexagons touch",
        (count) => {
            const { positions, width, height } = radialLayout(memoryRing(count));
            // A hexagon is 2 * MEMORY_RADIUS across; keep a visible gap between outlines.
            expect(closest(positions)).toBeGreaterThanOrEqual(MEMORY_RADIUS * 2 + 4);
            for (const p of Object.values(positions)) {
                expect(p.x).toBeGreaterThan(MEMORY_RADIUS);
                expect(p.x).toBeLessThan(width - MEMORY_RADIUS);
                expect(p.y).toBeGreaterThan(MEMORY_RADIUS);
                expect(p.y).toBeLessThan(height - MEMORY_RADIUS);
            }
        }
    );

    test("a crowded graph grows the canvas instead of overlapping", () => {
        const small = radialLayout(memoryRing(8));
        const big = radialLayout(memoryRing(49));
        expect(big.width * big.height).toBeGreaterThan(small.width * small.height);
    });

    test("a crowded first ring pushes the second ring outward rather than into it", () => {
        const nodes = [...memoryRing(30), ...Array.from({ length: 5 }, (_, i) => ({ id: `z${i}`, hops: 2 }))];
        const { positions } = radialLayout(nodes);
        expect(closest(positions)).toBeGreaterThanOrEqual(MEMORY_RADIUS * 2 + 4);
    });
});

describe("circularLayout", () => {
    const people = (count) =>
        Array.from({ length: count }, (_, i) => ({ id: `p${String(i).padStart(3, "0")}`, memoryCount: 100 - i }));

    test("a lone contributor is centred and an empty list is empty", () => {
        expect(circularLayout([]).positions).toEqual({});
        expect(circularLayout(people(1)).positions.p000).toEqual({ x: 360, y: 220 });
    });

    test("the busiest contributor is placed first, at the top", () => {
        const { positions } = circularLayout(people(6));
        expect(positions.p000.y).toBeLessThan(positions.p003.y);
        expect(positions.p000.x).toBeCloseTo(360, 0);
    });

    test("ordering does not depend on input order, and ties break by id", () => {
        const tied = [
            { id: "b", memoryCount: 5 },
            { id: "a", memoryCount: 5 },
            { id: "c", memoryCount: 9 },
        ];
        expect(circularLayout([...tied].reverse()).positions).toEqual(circularLayout(tied).positions);
    });

    test.each([6, 20, 60, 200])("%i contributors never overlap", (count) => {
        const { positions } = circularLayout(people(count));
        const pts = Object.values(positions);
        // Tiles are 100 x 36 rectangles: two overlap only if they overlap on both axes.
        for (let a = 0; a < pts.length; a += 1) {
            for (let b = a + 1; b < pts.length; b += 1) {
                const apartX = Math.abs(pts[a].x - pts[b].x) >= PERSON_HALF_WIDTH * 2;
                const apartY = Math.abs(pts[a].y - pts[b].y) >= PERSON_HALF_HEIGHT * 2;
                expect(apartX || apartY).toBe(true);
            }
        }
    });
});

describe("edges and shapes", () => {
    const hex = { kind: "memory", focal: false };
    const person = { kind: "person" };

    test("a hexagon has six vertices at the stated radius", () => {
        const points = hexPoints(100, 100, 24).split(" ").map((p) => p.split(",").map(Number));
        expect(points).toHaveLength(6);
        points.forEach(([x, y]) => expect(Math.hypot(x - 100, y - 100)).toBeCloseTo(24, 1));
        // Pointy top: the highest vertex is directly above the centre.
        const top = points.reduce((best, p) => (p[1] < best[1] ? p : best));
        expect(top[0]).toBeCloseTo(100, 1);
    });

    test("an edge stops at the outlines instead of running into the centres", () => {
        const line = edgeLine(hex, { x: 0, y: 0 }, hex, { x: 200, y: 0 });
        expect(line.x1).toBeGreaterThan(MEMORY_RADIUS);
        expect(line.x2).toBeLessThan(200 - MEMORY_RADIUS);
        expect(line.y1).toBe(0);
    });

    test("an edge leaves a person tile through its side, not its corner", () => {
        const line = edgeLine(person, { x: 0, y: 0 }, person, { x: 0, y: 200 });
        expect(line.y1).toBeGreaterThan(18); // half the tile height
        expect(line.y1).toBeLessThan(30);
    });

    test("an arrow head is a triangle whose tip is the end of the line", () => {
        const line = { x1: 0, y1: 0, x2: 100, y2: 0 };
        const [tip, left, right] = arrowHead(line).split(" ").map((p) => p.split(",").map(Number));
        expect(tip).toEqual([100, 0]);
        expect(left[0]).toBeLessThan(100);
        expect(left[1]).toBeCloseTo(-right[1], 1);
    });

    test("the midpoint is halfway", () => {
        expect(midpoint({ x1: 0, y1: 10, x2: 100, y2: 30 })).toEqual({ x: 50, y: 20 });
    });
});
