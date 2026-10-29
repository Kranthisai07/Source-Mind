import React from "react";
import { Link } from "react-router-dom";
import { X } from "lucide-react";
import { edgeLabel } from "./graphModel";

const Row = ({ label, children }) => (
    <div className="flex items-start justify-between gap-3 py-1.5 border-b border-sm-border last:border-0">
        <dt className="font-mono text-[10px] uppercase tracking-wider text-sm-text-secondary pt-0.5">{label}</dt>
        <dd className="text-[12.5px] text-sm-text text-right">{children}</dd>
    </div>
);

/** What the selected node is, and what it is joined to. Text only, so it is also the screen-reader view. */
export default function NodeDetailPanel({ node, nodes, edges, onClose, testId = "graph-detail" }) {
    if (!node) return null;
    const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
    const joined = edges
        .filter((e) => e.source === node.id || e.target === node.id)
        .map((e) => {
            const outgoing = e.source === node.id;
            const other = byId[outgoing ? e.target : e.source];
            return { edge: e, outgoing, other };
        })
        .filter((row) => row.other);

    return (
        <aside
            data-testid={testId}
            aria-label="Selected node"
            className="sm-card p-4 text-sm-text"
        >
            <div className="flex items-start justify-between gap-2 mb-3">
                <h4 className="text-[13.5px] font-semibold">
                    {node.kind === "person" ? node.name : `Memory v${node.version}`}
                </h4>
                <button
                    type="button"
                    onClick={onClose}
                    aria-label="Close details"
                    className="p-1 -m-1 rounded text-sm-text-secondary hover:text-sm-text"
                >
                    <X className="w-4 h-4" />
                </button>
            </div>

            <dl>
                {node.kind === "person" ? (
                    <Row label="Memories">{node.memoryCount}</Row>
                ) : (
                    <>
                        <Row label="State">
                            {node.focal ? "This memory · " : ""}
                            {node.isCurrent ? "Current" : "Superseded"}
                        </Row>
                        <Row label="Distance">{node.hops === 0 ? "—" : `${node.hops} ${node.hops === 1 ? "hop" : "hops"}`}</Row>
                        <Row label="Importance">{Number(node.importance).toFixed(2)}</Row>
                    </>
                )}
            </dl>

            {node.kind === "memory" && node.preview && (
                <p className="mt-3 text-[12.5px] leading-relaxed text-sm-text-secondary">{node.preview}</p>
            )}

            <p className="mt-4 mb-1.5 font-mono text-[10px] uppercase tracking-wider text-sm-text-secondary">
                {node.kind === "person" ? "Shares memories with" : "Connections"}
            </p>
            {joined.length === 0 ? (
                <p className="text-[12.5px] text-sm-text-secondary">None in view.</p>
            ) : (
                <ul className="space-y-1.5" data-testid={`${testId}-connections`}>
                    {joined.map(({ edge, outgoing, other }) => (
                        <li key={edge.id} className="text-[12.5px]">
                            <span className="font-mono text-[10px] uppercase tracking-wider text-sm-text-secondary">
                                {edge.type === "shared"
                                    ? `${edge.weight} shared`
                                    : `${edgeLabel(edge.type)} ${outgoing ? "→" : "←"}`}
                            </span>{" "}
                            {other.kind === "person" ? other.name : `v${other.version} ${other.preview ? `· ${other.preview.slice(0, 48)}` : ""}`}
                        </li>
                    ))}
                </ul>
            )}

            {node.kind === "memory" && !node.focal && (
                <Link
                    to={`/memories/${encodeURIComponent(node.id)}`}
                    className="mt-4 inline-flex text-[12.5px] font-medium text-sm-blue hover:underline"
                    data-testid={`${testId}-open`}
                >
                    Open this memory
                </Link>
            )}
        </aside>
    );
}
