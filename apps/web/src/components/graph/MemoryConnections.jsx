import React, { useState } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../ui/tabs";
import { PageLoadError } from "../widgets/PageLoadState";
import useApiResource from "../../hooks/useApiResource";
import api from "../../lib/api";
import { classifyApiError } from "../../lib/apiError";
import { formatDate } from "../../lib/format";
import GraphView from "./GraphView";
import { relatedToGraph } from "./graphModel";

const TRIGGER =
    "data-[state=active]:bg-sm-blue/15 data-[state=active]:text-sm-blue text-sm-text-secondary";

const SOURCE_NOTE = {
    graph: "from the graph",
    postgres: "from the database",
};

const pct = (weight) => `${Math.round(Number(weight) * 100)}%`;

function Related({ memoryId }) {
    const [depth, setDepth] = useState(1);
    const resource = useApiResource(() => api.getRelatedMemories(memoryId, { depth }), {
        resetKey: memoryId,
        deps: [depth],
    });
    const data = resource.data;
    const shallow = data && data.depth_served < data.depth_requested;
    return (
        <div className="space-y-3">
            <div role="group" aria-label="Hops to follow" className="flex items-center gap-2">
                <span className="font-mono text-[10px] uppercase tracking-wider text-sm-text-secondary">Hops</span>
                {[1, 2, 3].map((d) => (
                    <button
                        key={d}
                        type="button"
                        aria-pressed={depth === d}
                        data-testid={`related-depth-${d}`}
                        onClick={() => setDepth(d)}
                        className={`h-7 min-w-7 px-2 rounded border text-[12px] font-mono ${
                            depth === d
                                ? "bg-sm-text text-sm-bg border-sm-text"
                                : "border-sm-border text-sm-text-secondary hover:text-sm-text"
                        }`}
                    >
                        {d}
                    </button>
                ))}
            </div>
            <GraphView
                title="Related memories"
                subtitle="What this memory updates, extends or derives from, and what builds on it."
                resource={resource}
                build={relatedToGraph}
                kind="memory"
                layout="radial"
                emptyTitle="No related memories yet"
                emptyHint="Relationships appear when SourceMind finds memories that update, extend or derive from this one."
                note={
                    shallow
                        ? `Showing ${data.depth_served} hop. Following more than one needs the knowledge graph, which is not answering right now.`
                        : null
                }
                testId="related-graph"
            />
        </div>
    );
}

function Lineage({ memoryId, pilotMode }) {
    const resource = useApiResource(() => api.getMemoryLineage(memoryId), { resetKey: memoryId });
    const { data, error, retry } = resource;
    return (
        <section className="sm-card p-6" data-testid="lineage-panel">
            <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
                <div>
                    <h3 className="text-[15px] font-semibold text-sm-text">Lineage</h3>
                    <p className="mt-1 text-[12.5px] text-sm-text-secondary">
                        Every version of this memory, newest first, and who contributed to each.
                    </p>
                </div>
                {data?.source && (
                    <span className="gv-chip" data-testid="lineage-source" data-source={data.source}>
                        <span className="gv-chip-dot" aria-hidden="true" />
                        {SOURCE_NOTE[data.source]}
                    </span>
                )}
            </div>
            {error && !data ? (
                <PageLoadError error={classifyApiError(error)} onRetry={retry} compact testId="lineage-error" />
            ) : !data ? (
                <div role="status" aria-busy="true" data-testid="lineage-loading" className="h-40 rounded shimmer" />
            ) : data.versions.length === 0 ? (
                <p className="text-[12.5px] text-sm-text-secondary" data-testid="lineage-empty">
                    No version history is available.
                </p>
            ) : (
                <ol className="relative border-l-2 border-sm-border ml-2 space-y-5" data-testid="lineage-list">
                    {data.versions.map((v) => (
                        <li key={v.id} className="pl-5 relative" data-testid={`lineage-version-${v.version}`}>
                            <span
                                className={`absolute -left-[9px] top-1 w-4 h-4 rounded-full border-2 border-sm-bg ${
                                    v.is_current ? "bg-sm-blue" : "bg-sm-text-muted"
                                }`}
                            />
                            <div className="flex flex-wrap items-center gap-2 mb-1">
                                <span className="font-mono text-[11px] text-sm-text">v{v.version}</span>
                                <span className="font-mono text-[9.5px] uppercase tracking-wider text-sm-text-secondary">
                                    {v.is_current ? "Current" : "Historical"}
                                </span>
                                {v.created_at && (
                                    <time dateTime={v.created_at} className="font-mono text-[10.5px] text-sm-text-secondary">
                                        {formatDate(v.created_at)}
                                    </time>
                                )}
                            </div>
                            {!pilotMode &&
                                (v.contributors.length === 0 ? (
                                    <p className="text-[12.5px] text-sm-text-secondary">No contribution recorded.</p>
                                ) : (
                                    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-sm-text">
                                        {v.contributors.map((c) => (
                                            <li key={c.user_id}>
                                                {c.display_name || "A contributor"}{" "}
                                                <span className="font-mono text-[11px] text-sm-text-secondary">
                                                    {pct(c.weight)}
                                                    {c.action ? ` · ${c.action}` : ""}
                                                </span>
                                            </li>
                                        ))}
                                    </ul>
                                ))}
                        </li>
                    ))}
                </ol>
            )}
        </section>
    );
}

function Owners({ memoryId }) {
    const resource = useApiResource(() => api.getMemoryOwners(memoryId), { resetKey: memoryId });
    const { data, error, retry } = resource;
    const top = data?.owners?.[0]?.score || 1;
    return (
        <section className="sm-card p-6" data-testid="owners-panel">
            <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
                <div>
                    <h3 className="text-[15px] font-semibold text-sm-text">Who would know</h3>
                    <p className="mt-1 text-[12.5px] text-sm-text-secondary">
                        People who contributed to this memory, and to the memories connected to it. A contribution
                        counts half for each hop away.
                    </p>
                </div>
                {data?.source && (
                    <span className="gv-chip" data-testid="owners-source" data-source={data.source}>
                        <span className="gv-chip-dot" aria-hidden="true" />
                        {SOURCE_NOTE[data.source]}
                    </span>
                )}
            </div>
            {error && !data ? (
                <PageLoadError error={classifyApiError(error)} onRetry={retry} compact testId="owners-error" />
            ) : !data ? (
                <div role="status" aria-busy="true" data-testid="owners-loading" className="h-40 rounded shimmer" />
            ) : data.owners.length === 0 ? (
                <p className="text-[12.5px] text-sm-text-secondary" data-testid="owners-empty">
                    No one has a recorded contribution to this memory yet.
                </p>
            ) : (
                <ol className="divide-y divide-sm-border" data-testid="owners-list">
                    {data.owners.map((o, index) => (
                        <li key={o.user_id} className="py-3 flex items-center gap-4" data-testid={`owner-${index + 1}`}>
                            <span className="font-mono text-[11px] text-sm-text-secondary w-5">{index + 1}</span>
                            <div className="min-w-0 flex-1">
                                <p className="text-[13px] font-medium text-sm-text truncate">
                                    {o.display_name || "A contributor"}
                                </p>
                                <p className="text-[12px] text-sm-text-secondary">
                                    {o.hops === 0
                                        ? "Contributed to this memory"
                                        : `Contributed to a memory ${o.hops} ${o.hops === 1 ? "hop" : "hops"} away`}
                                    {o.hops > 0 && o.direct_weight != null ? " and to this one" : ""}
                                </p>
                            </div>
                            <div className="w-28 shrink-0">
                                <div className="h-1.5 rounded-full bg-sm-surface-2" aria-hidden="true">
                                    <div
                                        className="h-1.5 rounded-full bg-sm-text"
                                        style={{ width: `${Math.max(4, (o.score / top) * 100)}%` }}
                                    />
                                </div>
                                <p className="mt-1 text-right font-mono text-[10.5px] text-sm-text-secondary">
                                    score {o.score.toFixed(2)}
                                </p>
                            </div>
                        </li>
                    ))}
                </ol>
            )}
        </section>
    );
}

/**
 * The connections of one memory. Each tab mounts only when opened, so a memory page makes one
 * graph request, not three. Attribution (contributors, weights, who would know) is hidden in
 * pilot mode, like everywhere else.
 */
export default function MemoryConnections({ memoryId, pilotMode = false }) {
    return (
        <Tabs defaultValue="related" className="w-full" data-testid="memory-connections">
            <TabsList className="bg-sm-surface border border-sm-border p-1 h-10 mb-4" data-testid="connections-tabs">
                <TabsTrigger value="related" data-testid="tab-related" className={TRIGGER}>
                    Related
                </TabsTrigger>
                <TabsTrigger value="lineage" data-testid="tab-lineage" className={TRIGGER}>
                    Lineage
                </TabsTrigger>
                {!pilotMode && (
                    <TabsTrigger value="owners" data-testid="tab-owners" className={TRIGGER}>
                        Who would know
                    </TabsTrigger>
                )}
            </TabsList>
            <TabsContent value="related" className="mt-0">
                <Related memoryId={memoryId} />
            </TabsContent>
            <TabsContent value="lineage" className="mt-0">
                <Lineage memoryId={memoryId} pilotMode={pilotMode} />
            </TabsContent>
            {!pilotMode && (
                <TabsContent value="owners" className="mt-0">
                    <Owners memoryId={memoryId} />
                </TabsContent>
            )}
        </Tabs>
    );
}
