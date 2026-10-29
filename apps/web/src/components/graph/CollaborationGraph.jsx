import React from "react";
import useApiResource from "../../hooks/useApiResource";
import api from "../../lib/api";
import GraphView from "./GraphView";
import { collaborationToGraph } from "./graphModel";

/** Who works on the same memories as whom, across the workspace. */
export default function CollaborationGraph() {
    const resource = useApiResource(() => api.getCollaborationGraph(), {});
    return (
        <GraphView
            title="Collaboration"
            subtitle="People who contributed to the same current memories. Line width is the number of memories they share."
            resource={resource}
            build={collaborationToGraph}
            kind="people"
            layout="circular"
            emptyTitle="No collaboration yet"
            emptyHint="Once several people contribute to the same memories, they appear here."
            testId="collaboration-graph"
        />
    );
}
