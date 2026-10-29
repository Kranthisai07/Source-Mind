import {
    collaborationToGraph,
    describeNode,
    EDGE_TYPES,
    edgeLabel,
    neighbourhood,
    presentTypes,
    relatedToGraph,
    sharedWidth,
} from "./graphModel";

const related = {
    memory_id: "m1",
    source: "graph",
    depth_requested: 2,
    depth_served: 2,
    nodes: [
        { id: "m1", version: 3, is_current: true, importance: 0.8, hops: 0, preview: "The focal memory" },
        { id: "m2", version: 1, is_current: false, importance: 0.2, hops: 1, preview: "An older superseded memory with a rather long preview" },
    ],
    edges: [
        { id: "e1", source: "m1", target: "m2", type: "updates", confidence: 0.9 },
        { id: "e2", source: "m1", target: "ghost", type: "extends", confidence: null },
    ],
};

describe("relatedToGraph", () => {
    const graph = relatedToGraph(related);

    test("marks exactly the requested memory as focal", () => {
        expect(graph.nodes.filter((n) => n.focal).map((n) => n.id)).toEqual(["m1"]);
    });

    test("keeps only edges whose two ends are both drawn", () => {
        expect(graph.edges.map((e) => e.id)).toEqual(["e1"]);
    });

    test("labels are clipped, and fall back to the version when there is no preview", () => {
        const long = graph.nodes.find((n) => n.id === "m2");
        expect(long.label.length).toBeLessThanOrEqual(22);
        expect(long.label.endsWith("…")).toBe(true);
        expect(relatedToGraph({ memory_id: "x", nodes: [{ id: "x", version: 4, hops: 0 }] }).nodes[0].label).toBe("v4");
    });

    test("a missing response is an empty graph, not an exception", () => {
        expect(relatedToGraph(undefined)).toEqual({ nodes: [], edges: [] });
    });

    test("superseded and current memories are told apart", () => {
        expect(graph.nodes.map((n) => n.isCurrent)).toEqual([true, false]);
    });
});

describe("collaborationToGraph", () => {
    const graph = collaborationToGraph({
        source: "postgres",
        nodes: [
            { user_id: "u1", display_name: "Ada Lovelace-Byron", memory_count: 12 },
            { user_id: "u2", display_name: null, memory_count: 1 },
        ],
        edges: [
            { source: "u1", target: "u2", shared_memories: 4 },
            { source: "u1", target: "nobody", shared_memories: 9 },
        ],
    });

    test("people become person nodes, with a short id when there is no name", () => {
        expect(graph.nodes.map((n) => n.kind)).toEqual(["person", "person"]);
        expect(graph.nodes[0].label.length).toBeLessThanOrEqual(14);
        expect(graph.nodes[1].name).toBe("u2");
    });

    test("edges to people who are not drawn are dropped, and carry their weight", () => {
        expect(graph.edges).toEqual([{ id: "u1~u2", source: "u1", target: "u2", type: "shared", weight: 4 }]);
    });
});

describe("the vocabulary", () => {
    test("uses Supermemory's own words for the three relationships it shares with us", () => {
        expect(EDGE_TYPES.updates.label).toBe("Updates");
        expect(EDGE_TYPES.extends.label).toBe("Extends");
        expect(EDGE_TYPES.derives.label).toBe("Derives");
        expect(EDGE_TYPES.updates.blurb).toMatch(/history is kept/i);
    });

    test("only updates and derives have a direction", () => {
        const directed = Object.entries(EDGE_TYPES).filter(([, v]) => v.directed).map(([k]) => k);
        expect(directed.sort()).toEqual(["derives", "updates"]);
    });

    test("an unknown type still gets a label", () => {
        expect(edgeLabel("something_new")).toBe("something_new");
        expect(edgeLabel(undefined)).toBe("related");
    });

    test("the legend lists only the types that are drawn, in a stable order", () => {
        expect(presentTypes([{ type: "references" }, { type: "updates" }, { type: "updates" }])).toEqual([
            "updates",
            "references",
        ]);
        expect(presentTypes([])).toEqual([]);
    });
});

describe("describeNode and neighbourhood", () => {
    test("describes a memory in words a screen reader can use", () => {
        const text = describeNode({ kind: "memory", focal: true, version: 3, isCurrent: true, hops: 0, preview: "Hello" });
        expect(text).toBe("This memory, Memory version 3, current, Hello");
        const other = describeNode({ kind: "memory", version: 1, isCurrent: false, hops: 2, preview: "" });
        expect(other).toBe("Memory version 1, superseded, 2 hops away");
    });

    test("pluralises people's memory counts", () => {
        expect(describeNode({ kind: "person", name: "Ada", memoryCount: 1 })).toBe("Ada, 1 memory");
        expect(describeNode({ kind: "person", name: "Ada", memoryCount: 3 })).toBe("Ada, 3 memories");
    });

    test("a node's neighbourhood is itself, what it touches, and those edges", () => {
        const edges = [
            { id: "1", source: "a", target: "b" },
            { id: "2", source: "c", target: "a" },
            { id: "3", source: "c", target: "d" },
        ];
        const lit = neighbourhood(edges, "a");
        expect([...lit.nodeIds].sort()).toEqual(["a", "b", "c"]);
        expect([...lit.edgeIds].sort()).toEqual(["1", "2"]);
    });

    test("shared-memory line width grows slowly and is capped", () => {
        expect(sharedWidth(1)).toBe(1);
        expect(sharedWidth(4)).toBe(3);
        expect(sharedWidth(10_000)).toBe(5);
        expect(sharedWidth(0)).toBe(1);
    });
});
