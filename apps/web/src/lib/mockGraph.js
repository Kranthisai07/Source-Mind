// Mock-mode answers for the graph-backed read endpoints. Same shapes as the real API
// (api/v1/graph.py), derived deterministically from the mock memories and contributors, so the
// graph views can be developed and tested without a backend.

import { CONTRIBUTORS, MEMORIES } from "./mockData";

const RELATION_CYCLE = ["updates", "extends", "derives", "references", "contradicts"];
const MAX_DEPTH = 3;
const MIN_WEIGHT = 0.05;

const preview = (memory) => (memory.content || "").replace(/\s+/g, " ").slice(0, 120);
const person = (login) => CONTRIBUTORS.find((c) => c.login === login) || CONTRIBUTORS[0];

function find(id) {
    const index = MEMORIES.findIndex((m) => m.memory_id === id);
    if (index < 0) {
        const error = new Error("Memory not found");
        error.status = 404; // like require_memory_access: "no such memory for you"
        throw error;
    }
    return { memory: MEMORIES[index], index };
}

const node = (memory, hops) => ({
    id: memory.memory_id,
    version: memory.version,
    is_current: true,
    importance: +(0.35 + ((memory.version * 7) % 10) / 20).toFixed(2),
    hops,
    preview: preview(memory),
});

/** A small deterministic neighbourhood: the next memories in the list, spread over hop rings. */
export function mockRelated(id, { depth = 1, types, limit = 25 } = {}) {
    const { memory, index } = find(id);
    const wanted = Array.isArray(types) && types.length ? new Set(types) : null;
    const reach = Math.max(1, Math.min(Number(depth) || 1, MAX_DEPTH));
    const rings = { 1: 3, 2: 2, 3: 2 };
    const nodes = [node(memory, 0)];
    const edges = [];
    let cursor = index;
    let previousRing = [memory];
    for (let hop = 1; hop <= reach; hop += 1) {
        const ring = [];
        for (let k = 0; k < rings[hop]; k += 1) {
            cursor = (cursor + 1) % MEMORIES.length;
            const next = MEMORIES[cursor];
            if (next.memory_id === id) continue;
            const type = RELATION_CYCLE[(index + nodes.length) % RELATION_CYCLE.length];
            const from = previousRing[k % previousRing.length];
            if (wanted && !wanted.has(type)) continue;
            nodes.push(node(next, hop));
            ring.push(next);
            edges.push({
                id: `rel-${from.memory_id}-${next.memory_id}`,
                source: from.memory_id,
                target: next.memory_id,
                type,
                confidence: +(0.97 - hop * 0.07 - k * 0.02).toFixed(2),
            });
        }
        if (ring.length) previousRing = ring;
    }
    const visible = nodes.slice(0, Math.max(1, Number(limit) || 25));
    const ids = new Set(visible.map((n) => n.id));
    return {
        memory_id: id,
        source: "graph",
        depth_requested: Number(depth) || 1,
        depth_served: reach,
        nodes: visible,
        edges: edges.filter((e) => ids.has(e.source) && ids.has(e.target)),
        truncated: visible.length < nodes.length,
    };
}

const contributor = (entry) => ({
    user_id: person(entry.author).id,
    display_name: entry.name,
    weight: entry.score,
    action: "edit",
});

export function mockLineage(id) {
    const { memory } = find(id);
    const versions = (memory.versions || []).map((v, i) => ({
        id: `${id}-v${v.v ?? i + 1}`,
        version: v.v ?? i + 1,
        is_current: i === 0,
        created_at: v.at ?? null,
        contributors: i === 0 ? (memory.attribution || []).map(contributor) : [
            { user_id: person(v.editor).id, display_name: person(v.editor).name, weight: 1, action: "edit" },
        ],
    }));
    return { memory_id: id, source: "graph", versions };
}

export function mockOwners(id) {
    const { memory, index } = find(id);
    const neighbour = MEMORIES[(index + 1) % MEMORIES.length];
    const scores = new Map();
    const add = (entry, hops, via) => {
        if (entry.score < MIN_WEIGHT) return;
        const user = person(entry.author);
        const current = scores.get(user.id) || {
            user_id: user.id, display_name: entry.name, score: 0, direct_weight: null, hops, path: via,
        };
        current.score += entry.score * 0.5 ** hops;
        if (hops === 0) current.direct_weight = entry.score;
        if (hops < current.hops) Object.assign(current, { hops, path: via });
        scores.set(user.id, current);
    };
    (memory.attribution || []).forEach((entry) => add(entry, 0, [id]));
    (neighbour.attribution || []).forEach((entry) => add(entry, 1, [id, neighbour.memory_id]));
    const owners = [...scores.values()]
        .map((o) => ({ ...o, score: +o.score.toFixed(6) }))
        .sort((a, b) => b.score - a.score || a.user_id.localeCompare(b.user_id));
    return { memory_id: id, source: "graph", depth_served: 2, owners };
}

export function mockCollaboration() {
    const nodes = CONTRIBUTORS.map((c) => ({
        user_id: c.id, display_name: c.name, memory_count: c.count,
    })).sort((a, b) => b.memory_count - a.memory_count || a.user_id.localeCompare(b.user_id));
    const edges = [];
    for (let a = 0; a < nodes.length; a += 1) {
        for (let b = a + 1; b < nodes.length; b += 1) {
            const shared = Math.floor(Math.min(nodes[a].memory_count, nodes[b].memory_count) / (4 + a + b));
            if (shared > 0) edges.push({ source: nodes[a].user_id, target: nodes[b].user_id, shared_memories: shared });
        }
    }
    edges.sort((x, y) => y.shared_memories - x.shared_memories);
    return { source: "graph", nodes, edges: edges.slice(0, 14), truncated: false };
}
