import React from "react";
import { Check, Loader2, Circle, AlertCircle } from "lucide-react";

// 7-stage ingest pipeline: receive → extract → chunk → embed → store → attribute → done.
const LABELS = {
    receive:   "Receive",
    extract:   "Extract",
    chunk:     "Chunk",
    embed:     "Embed",
    store:     "Store",
    attribute: "Attribute",
    done:      "Done",
};

export default function PipelineTracker({ stages = [], currentStage = "receive" }) {
    return (
        <div className="space-y-1.5" data-testid="pipeline-tracker">
            {stages.map((s, i) => {
                const status = s.status;
                const color =
                    status === "done"     ? "#34D399" :
                    status === "running"  ? "#4F7EFF" :
                    status === "error"    ? "#EF4444" :
                                            "#4A4A6A";
                const Icon =
                    status === "done"    ? Check :
                    status === "running" ? Loader2 :
                    status === "error"   ? AlertCircle :
                                            Circle;
                return (
                    <div
                        key={i}
                        className="flex items-center gap-3 px-3 py-2 rounded-lg border border-sm-border bg-sm-bg/40"
                        data-testid={`pipeline-stage-${s.name}`}
                    >
                        <div
                            className="w-7 h-7 rounded-md flex items-center justify-center shrink-0"
                            style={{ background: `${color}20`, color }}
                        >
                            <Icon className={`w-3.5 h-3.5 ${status === "running" ? "animate-spin" : ""}`} strokeWidth={2.5} />
                        </div>
                        <div className="flex-1 min-w-0 flex items-center justify-between gap-2">
                            <span className="text-[12.5px] font-medium text-sm-text">{LABELS[s.name] || s.name}</span>
                            <span className="font-mono text-[10.5px] text-sm-text-secondary">
                                {status === "pending" ? "—" : `${s.elapsed_ms}ms`}
                            </span>
                        </div>
                        <span
                            className="w-1.5 h-1.5 rounded-full shrink-0"
                            style={{ background: color }}
                        />
                    </div>
                );
            })}
        </div>
    );
}
