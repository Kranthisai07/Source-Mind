import React, { useMemo, useState } from "react";
import "./graph.css";
import { PageLoadError } from "../widgets/PageLoadState";
import { classifyApiError } from "../../lib/apiError";
import GraphCanvas from "./GraphCanvas";
import Legend from "./Legend";
import NodeDetailPanel from "./NodeDetailPanel";
import { edgeLabel } from "./graphModel";

const SOURCE_TEXT = {
    graph: {
        label: "Graph",
        title: "Answered from the knowledge graph.",
    },
    postgres: {
        label: "Database",
        title:
            "Answered from the database. The graph is switched off, still catching up, or disagreed with the database about this memory.",
    },
};

function SourceChip({ source, testId }) {
    const info = SOURCE_TEXT[source];
    if (!info) return null;
    return (
        <span
            className={`gv-chip ${source === "graph" ? "gv-chip-graph" : ""}`}
            title={info.title}
            data-testid={testId}
            data-source={source}
        >
            <span className="gv-chip-dot" aria-hidden="true" />
            {info.label}
        </span>
    );
}

/** The accessible twin of the picture: the same nodes and links as plain table rows. */
function GraphList({ graph, kind, testId }) {
    const byId = Object.fromEntries(graph.nodes.map((n) => [n.id, n]));
    const links = (id) =>
        graph.edges
            .filter((e) => e.source === id || e.target === id)
            .map((e) => {
                const other = byId[e.source === id ? e.target : e.source];
                if (!other) return null;
                const name = other.kind === "person" ? other.name : `v${other.version}`;
                return e.type === "shared" ? `${name} (${e.weight})` : `${edgeLabel(e.type)} ${name}`;
            })
            .filter(Boolean)
            .join(", ");
    return (
        <div className="overflow-x-auto" data-testid={testId}>
            <table className="w-full text-[12.5px]">
                <thead>
                    <tr className="text-left font-mono text-[10.5px] uppercase tracking-wider text-sm-text-secondary border-b border-sm-border">
                        {kind === "memory" ? (
                            <>
                                <th className="py-2 pr-3 font-normal">Memory</th>
                                <th className="py-2 pr-3 font-normal">State</th>
                                <th className="py-2 pr-3 font-normal">Hops</th>
                                <th className="py-2 font-normal">Connected by</th>
                            </>
                        ) : (
                            <>
                                <th className="py-2 pr-3 font-normal">Contributor</th>
                                <th className="py-2 pr-3 font-normal">Memories</th>
                                <th className="py-2 font-normal">Shares memories with</th>
                            </>
                        )}
                    </tr>
                </thead>
                <tbody>
                    {graph.nodes.map((n) => (
                        <tr key={n.id} className="border-b border-sm-border last:border-0 align-top">
                            {kind === "memory" ? (
                                <>
                                    <td className="py-2 pr-3 text-sm-text">
                                        <span className="font-mono text-[11px] text-sm-text-secondary">v{n.version}</span>{" "}
                                        {n.focal ? <strong>This memory</strong> : n.preview.slice(0, 80)}
                                    </td>
                                    <td className="py-2 pr-3 text-sm-text-secondary">
                                        {n.isCurrent ? "Current" : "Superseded"}
                                    </td>
                                    <td className="py-2 pr-3 text-sm-text-secondary">{n.hops}</td>
                                    <td className="py-2 text-sm-text-secondary">{links(n.id) || "—"}</td>
                                </>
                            ) : (
                                <>
                                    <td className="py-2 pr-3 text-sm-text">{n.name}</td>
                                    <td className="py-2 pr-3 text-sm-text-secondary">{n.memoryCount}</td>
                                    <td className="py-2 text-sm-text-secondary">{links(n.id) || "—"}</td>
                                </>
                            )}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

// More nodes than this is a wall of shapes: open as the list (the picture is one click away).
export const LARGE_GRAPH = 40;

const prefersList = () =>
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(max-width: 640px)").matches;

/**
 * One panel for every graph in the app: loading, error with retry, empty, then the picture with a
 * legend and a detail panel, plus the same data as a list. While a refetch is in flight the last
 * picture stays on screen, dimmed, instead of flashing a skeleton.
 *
 * `build(data)` turns a response into {nodes, edges}; `source` and `note` come from the response.
 */
export default function GraphView({
    title,
    subtitle,
    resource,
    build,
    kind = "memory",
    layout = "radial",
    emptyTitle = "Nothing to show yet",
    emptyHint = "",
    note = null,
    testId = "graph-view",
}) {
    const { data, error, loading, retry } = resource;
    const [selectedId, setSelectedId] = useState(null);
    const [chosenView, setChosenView] = useState(null);

    const graph = useMemo(() => (data ? build(data) : null), [data, build]);
    const view = chosenView || (prefersList() || (graph && graph.nodes.length > LARGE_GRAPH) ? "list" : "graph");
    const setView = setChosenView;
    const selected = graph?.nodes.find((n) => n.id === selectedId) || null;
    const empty = graph && (kind === "memory" ? graph.nodes.length <= 1 : graph.nodes.length === 0);

    let body;
    if (error && !data) {
        body = (
            <PageLoadError
                error={classifyApiError(error)}
                onRetry={retry}
                compact
                testId={`${testId}-error`}
            />
        );
    } else if (!graph) {
        body = (
            <div
                role="status"
                aria-busy="true"
                aria-label={`Loading ${title}`}
                data-testid={`${testId}-loading`}
                className="sm-card h-[320px] shimmer"
            />
        );
    } else if (empty) {
        body = (
            <div data-testid={`${testId}-empty`} className="sm-card p-8 text-center">
                <p className="text-[14px] font-semibold text-sm-text">{emptyTitle}</p>
                {emptyHint && <p className="mt-1 text-[12.5px] text-sm-text-secondary">{emptyHint}</p>}
            </div>
        );
    } else {
        body = (
            <div className={loading ? "opacity-60 transition-opacity" : "transition-opacity"} aria-busy={loading}>
                {view === "graph" ? (
                    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_280px] items-start">
                        <div className="gv-frame gv-field">
                            <GraphCanvas
                                nodes={graph.nodes}
                                edges={graph.edges}
                                layout={layout}
                                selectedId={selectedId}
                                onSelect={setSelectedId}
                                label={title}
                                testId={`${testId}-canvas`}
                            />
                        </div>
                        {selected ? (
                            <NodeDetailPanel
                                node={selected}
                                nodes={graph.nodes}
                                edges={graph.edges}
                                onClose={() => setSelectedId(null)}
                                testId={`${testId}-detail`}
                            />
                        ) : (
                            <p className="text-[12.5px] text-sm-text-secondary" data-testid={`${testId}-hint`}>
                                Select a node to see what it is joined to. Arrow keys move between nodes.
                            </p>
                        )}
                    </div>
                ) : (
                    <GraphList graph={graph} kind={kind} testId={`${testId}-list`} />
                )}
                {view === "graph" && (
                    <div className="mt-5 pt-5 border-t border-sm-border">
                        <Legend edges={graph.edges} kind={kind} testId={`${testId}-legend`} />
                    </div>
                )}
                {data?.truncated && (
                    <p className="mt-3 text-[12px] text-sm-text-secondary" data-testid={`${testId}-truncated`}>
                        Showing the most relevant part; there is more than fits here.
                    </p>
                )}
            </div>
        );
    }

    return (
        <section className="sm-card p-6" data-testid={testId}>
            <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
                <div>
                    <h3 className="text-[15px] font-semibold text-sm-text">{title}</h3>
                    {subtitle && <p className="mt-1 text-[12.5px] text-sm-text-secondary">{subtitle}</p>}
                </div>
                <div className="flex items-center gap-2">
                    {data?.source && <SourceChip source={data.source} testId={`${testId}-source`} />}
                    {graph && !empty && (
                        <div role="group" aria-label="View" className="inline-flex rounded-md border border-sm-border p-0.5">
                            {["graph", "list"].map((v) => (
                                <button
                                    key={v}
                                    type="button"
                                    aria-pressed={view === v}
                                    onClick={() => setView(v)}
                                    data-testid={`${testId}-view-${v}`}
                                    className={`px-2.5 py-1 rounded text-[11px] font-mono uppercase tracking-wider ${
                                        view === v ? "bg-sm-text text-sm-bg" : "text-sm-text-secondary hover:text-sm-text"
                                    }`}
                                >
                                    {v}
                                </button>
                            ))}
                        </div>
                    )}
                </div>
            </div>
            {note && graph && !empty && (
                <p className="mb-4 text-[12.5px] text-sm-text-secondary" data-testid={`${testId}-note`}>
                    {note}
                </p>
            )}
            {body}
        </section>
    );
}
