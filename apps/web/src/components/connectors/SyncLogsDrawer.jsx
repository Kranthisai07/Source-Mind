import React, { useEffect, useState } from "react";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "../ui/sheet";
import StatusBadge from "../widgets/StatusBadge";
import api from "../../lib/api";
import { classifyApiError } from "../../lib/apiError";
import { FILTER_LABEL, describeErrorCode, durationLabel, formatCount } from "./connectorText";

const dateLabel = (iso) => {
    const t = Date.parse(iso);
    return Number.isFinite(t) ? new Date(t).toLocaleString() : "—";
};

// Reads the real SyncLogsResponse: {items: SyncLogResponse[], total}. Every
// field is treated as possibly absent; a row never throws. The backend's
// error_message is deliberately generic, so only error_code (mapped) is shown.
export default function SyncLogsDrawer({ open, onOpenChange, connector }) {
    const [logs, setLogs] = useState([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);

    useEffect(() => {
        if (!open || !connector) return undefined;
        let cancelled = false;
        setLoading(true);
        setError(null);
        setLogs([]);
        Promise.resolve()
            .then(() => api.getSyncLogs(connector.id))
            .then(
                (response) => {
                    if (cancelled) return;
                    setLogs(Array.isArray(response?.items) ? response.items : []);
                },
                (requestError) => {
                    if (cancelled) return;
                    setError(classifyApiError(requestError));
                }
            )
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, [open, connector]);

    return (
        <Sheet open={open} onOpenChange={onOpenChange}>
            <SheetContent side="right" className="sm:max-w-[680px] w-[680px] bg-sm-surface border-l border-sm-border text-sm-text p-0 overflow-y-auto" data-testid="sync-logs-drawer">
                <SheetHeader className="p-6 border-b border-sm-border">
                    <SheetTitle className="text-sm-text text-[17px] font-semibold">Sync logs</SheetTitle>
                    <SheetDescription className="sr-only">Recent sync runs for this connector.</SheetDescription>
                    <div className="font-mono text-[11.5px] text-sm-text-secondary">{connector?.display_name}</div>
                </SheetHeader>
                <div className="p-6">
                    {loading && <p data-testid="sync-logs-loading" className="text-[12.5px] text-sm-text-secondary">Loading…</p>}
                    {error && (
                        <p role="alert" data-testid="sync-logs-error" className="text-[12.5px] text-sm-red">
                            {error.title}. {error.detail}
                        </p>
                    )}
                    {!loading && !error && logs.length === 0 && (
                        <p data-testid="sync-logs-empty" className="text-[12.5px] text-sm-text-secondary">No syncs have run yet.</p>
                    )}
                    {logs.length > 0 && (
                        <table className="w-full text-[12px]">
                            <thead>
                                <tr className="text-left font-mono text-[10.5px] uppercase tracking-wider text-sm-text-secondary border-b border-sm-border">
                                    <th className="py-2.5">Started</th>
                                    <th className="py-2.5">Status</th>
                                    <th className="py-2.5">Trigger</th>
                                    <th className="py-2.5 text-right">Found</th>
                                    <th className="py-2.5 text-right">New</th>
                                    <th className="py-2.5 text-right">Updated</th>
                                    <th className="py-2.5 text-right">Skipped</th>
                                    <th className="py-2.5 text-right">Failed</th>
                                    <th className="py-2.5 text-right">Took</th>
                                </tr>
                            </thead>
                            <tbody>
                                {logs.map((l, i) => {
                                    const filtered = l?.artifacts_filtered && typeof l.artifacts_filtered === "object"
                                        ? Object.entries(l.artifacts_filtered) : [];
                                    const codeText = describeErrorCode(l?.error_code)
                                        ?? (l?.status === "failed" ? "The sync failed (no error code recorded)." : null);
                                    return (
                                        <React.Fragment key={l?.id || i}>
                                            <tr data-testid={`sync-log-row-${i}`} className="border-b border-sm-border/50">
                                                <td className="py-3 font-mono text-[11px] text-sm-text">{dateLabel(l?.started_at)}</td>
                                                <td className="py-3"><StatusBadge status={l?.status || "unknown"} /></td>
                                                <td className="py-3 font-mono text-[11px] text-sm-text-secondary">{l?.trigger || "—"}</td>
                                                <td className="py-3 text-right font-mono">{formatCount(l?.artifacts_found)}</td>
                                                <td className="py-3 text-right font-mono">{formatCount(l?.artifacts_new)}</td>
                                                <td className="py-3 text-right font-mono">{formatCount(l?.artifacts_updated)}</td>
                                                <td className="py-3 text-right font-mono">{formatCount(l?.artifacts_skipped)}</td>
                                                <td className="py-3 text-right font-mono">{formatCount(l?.artifacts_failed)}</td>
                                                <td className="py-3 text-right font-mono text-sm-text-secondary">
                                                    {l?.completed_at ? durationLabel(l.started_at, l.completed_at) : l?.status === "running" ? "running" : "—"}
                                                </td>
                                            </tr>
                                            {(codeText || filtered.length > 0) && (
                                                <tr data-testid={`sync-log-detail-${i}`} className="border-b border-sm-border/50">
                                                    <td colSpan={9} className="pb-3 text-[11.5px]">
                                                        {codeText && (
                                                            <div className="text-sm-red">
                                                                {codeText}
                                                                {l?.error_code && <span className="font-mono text-[10px] text-sm-text-muted"> ({l.error_code})</span>}
                                                            </div>
                                                        )}
                                                        {filtered.length > 0 && (
                                                            <div className="text-sm-text-secondary">
                                                                Left out on purpose:{" "}
                                                                {filtered.map(([reason, n], k) => (
                                                                    <span key={reason}>{k > 0 ? ", " : ""}{formatCount(n)} {FILTER_LABEL[reason] || reason}</span>
                                                                ))}
                                                            </div>
                                                        )}
                                                    </td>
                                                </tr>
                                            )}
                                        </React.Fragment>
                                    );
                                })}
                            </tbody>
                        </table>
                    )}
                </div>
            </SheetContent>
        </Sheet>
    );
}
