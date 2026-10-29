// Turns the API's graph responses into the nodes and edges the canvas draws.
//
// The wording for the relationship kinds follows Supermemory's own: new information UPDATES what
// was true before (history is kept), EXTENDS it with detail, or DERIVES something inferred from it.
// This product also records contradictions and plain references, so those are described too.

export const EDGE_TYPES = {
    updates: {
        label: "Updates",
        blurb: "Information changed. The new memory supersedes the old; history is kept.",
        directed: true,
    },
    extends: {
        label: "Extends",
        blurb: "Adds detail without invalidating the other memory. Both stay valid.",
        directed: false,
    },
    derives: {
        label: "Derives",
        blurb: "Inferred from the other memory, for example a merge of two.",
        directed: true,
    },
    contradicts: {
        label: "Contradicts",
        blurb: "The two memories disagree. Open conflicts are listed under Conflicts.",
        directed: false,
    },
    references: {
        label: "References",
        blurb: "Mentions the other memory without changing or extending it.",
        directed: false,
    },
};

export const edgeLabel = (type) => EDGE_TYPES[type]?.label || String(type || "related");

const clip = (text, max) => {
    // Previews are raw markdown; drop the markers so a label reads as words, not syntax.
    const clean = String(text || "").replace(/[#*_`>]+/g, "").replace(/\s+/g, " ").trim();
    return clean.length > max ? `${clean.slice(0, max - 1)}…` : clean;
};

const shortId = (id) => String(id || "").slice(0, 8);

/** GET /memories/:id/related -> { nodes, edges } for the canvas. */
export function relatedToGraph(response) {
    const focalId = response?.memory_id;
    const nodes = (response?.nodes || []).map((n) => ({
        id: n.id,
        kind: "memory",
        focal: n.id === focalId,
        hops: n.hops ?? 0,
        version: n.version,
        isCurrent: n.is_current !== false,
        importance: n.importance ?? 0,
        preview: n.preview || "",
        label: clip(n.preview, 22) || `v${n.version}`,
    }));
    const known = new Set(nodes.map((n) => n.id));
    const edges = (response?.edges || [])
        .filter((e) => known.has(e.source) && known.has(e.target))
        .map((e) => ({
            id: e.id,
            source: e.source,
            target: e.target,
            type: e.type,
            confidence: e.confidence ?? null,
        }));
    return { nodes, edges };
}

/** GET /workspaces/:id/analytics/collaboration-graph -> { nodes, edges }. */
export function collaborationToGraph(response) {
    const nodes = (response?.nodes || []).map((n) => ({
        id: n.user_id,
        kind: "person",
        focal: false,
        hops: 1,
        memoryCount: n.memory_count ?? 0,
        label: clip(n.display_name, 14) || shortId(n.user_id),
        name: n.display_name || shortId(n.user_id),
    }));
    const known = new Set(nodes.map((n) => n.id));
    const edges = (response?.edges || [])
        .filter((e) => known.has(e.source) && known.has(e.target))
        .map((e) => ({
            id: `${e.source}~${e.target}`,
            source: e.source,
            target: e.target,
            type: "shared",
            weight: e.shared_memories ?? 1,
        }));
    return { nodes, edges };
}

/** Stroke width for a shared-memories count: visible at 1, never dominating. */
export const sharedWidth = (count) => Math.min(5, 1 + Math.log2(Math.max(1, count)));

/** Which relation types actually occur, in a stable order, so the legend lists only what is drawn. */
export function presentTypes(edges) {
    const seen = new Set(edges.map((e) => e.type));
    return Object.keys(EDGE_TYPES).filter((type) => seen.has(type));
}

/** Plain-text description of a node, for the native tooltip and the screen-reader label. */
export function describeNode(node) {
    if (node.kind === "person") {
        return `${node.name}, ${node.memoryCount} ${node.memoryCount === 1 ? "memory" : "memories"}`;
    }
    const state = node.isCurrent ? "current" : "superseded";
    const parts = [`Memory version ${node.version}`, state];
    if (node.focal) parts.unshift("This memory");
    if (node.hops > 0) parts.push(`${node.hops} ${node.hops === 1 ? "hop" : "hops"} away`);
    if (node.preview) parts.push(node.preview);
    return parts.join(", ");
}

/** Edges touching a node, and the ids on the other end: what to keep lit on hover and focus. */
export function neighbourhood(edges, id) {
    const touching = edges.filter((e) => e.source === id || e.target === id);
    const ids = new Set([id]);
    touching.forEach((e) => {
        ids.add(e.source);
        ids.add(e.target);
    });
    return { edgeIds: new Set(touching.map((e) => e.id)), nodeIds: ids };
}
