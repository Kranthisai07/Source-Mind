import React, { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw, Github, FileText, Pause, Play, ScanSearch, RotateCw, Unplug } from "lucide-react";
import { toast } from "sonner";
import StatusBadge from "../widgets/StatusBadge";
import { Button } from "../ui/button";
import api from "../../lib/api";
import { relativeTime } from "../../lib/format";
import {
    ARTIFACT_LABEL,
    FILTER_LABEL,
    SCOPE_STATUS_LABEL,
    SCOPE_STATUS_TEXT,
    STATE_LABEL,
    describeConnectorError,
    describeErrorCode,
    formatCount,
} from "./connectorText";

// While a sync runs the card re-reads /status at this interval. One timer at a
// time, cleared on unmount; a failed read keeps the loop going (it is the only
// way the card learns the run ended) but never faster than this.
export const POLL_INTERVAL_MS = 4000;
// After "Sync now" the worker may not have started yet; watch this long for the
// run to appear before going quiet.
const WATCH_AFTER_TRIGGER_MS = 30000;

const when = (iso) => (iso ? relativeTime(iso) : "never");
const whenFuture = (iso) => {
    if (!iso) return "not scheduled";
    const diff = Date.parse(iso) - Date.now();
    if (!Number.isFinite(diff)) return "not scheduled";
    if (diff <= 0) return "due now";
    const min = Math.round(diff / 60000);
    return min < 60 ? `in ${min} min` : `in ${Math.round(min / 60)}h`;
};

export default function ConnectorCard({ connector, onLogs, onDisconnect, onChanged }) {
    const c = connector;
    const isGithub = c.connector_type === "github";
    const [status, setStatus] = useState(null);
    const [statusError, setStatusError] = useState(false);
    const [tick, setTick] = useState(0);
    const [watchUntil, setWatchUntil] = useState(0);
    const [busy, setBusy] = useState(null); // "sync" | "retry" | "rescan" | "pause"
    const [actionError, setActionError] = useState(null);
    const alive = useRef(true);
    const readGeneration = useRef(0);

    const refresh = useCallback(async () => {
        const mine = ++readGeneration.current;
        try {
            const next = await api.getConnectorStatus(c.id);
            if (!alive.current || mine !== readGeneration.current) return;
            setStatus(next);
            setStatusError(false);
        } catch {
            if (!alive.current || mine !== readGeneration.current) return;
            // Keep the last good status; say it may be stale.
            setStatusError(true);
        } finally {
            if (alive.current) setTick((n) => n + 1);
        }
    }, [c.id]);

    useEffect(() => {
        alive.current = true;
        refresh();
        return () => { alive.current = false; };
    }, [refresh]);

    // The list owns the connector's lifecycle status (a Disconnect or Pause elsewhere changes it);
    // the card caches its own status read and prefers it. When the list's status changes, read
    // again, otherwise the card keeps showing the old state until the page is reloaded.
    const listStatus = useRef(c.status);
    useEffect(() => {
        if (listStatus.current === c.status) return;
        listStatus.current = c.status;
        refresh();
    }, [c.status, refresh]);

    const state = status?.state ?? null;
    const syncing = state === "syncing";
    const watching = watchUntil > Date.now();

    // Poll only while a run is in progress (or just requested). Every completed
    // read bumps `tick`, which re-arms exactly one timer.
    useEffect(() => {
        if (!isGithub || !(syncing || watching)) return undefined;
        const timer = setTimeout(refresh, POLL_INTERVAL_MS);
        return () => clearTimeout(timer);
    }, [isGithub, syncing, watching, tick, refresh]);

    const run = async (name, work, onOk) => {
        if (busy) return;
        setBusy(name);
        setActionError(null);
        try {
            const result = await work();
            if (alive.current) onOk?.(result);
        } catch (error) {
            if (alive.current) setActionError(describeConnectorError(error, "That did not work."));
        } finally {
            if (alive.current) setBusy(null);
        }
    };

    const syncNow = () => run("sync", () => api.triggerSync(c.id), () => {
        toast.success("Sync queued", { description: "It starts when a worker picks it up." });
        setWatchUntil(Date.now() + WATCH_AFTER_TRIGGER_MS);
        refresh();
    });
    const retryFailed = () => run("retry", () => api.retryFailedConnector(c.id), (r) => {
        toast.success(`Requeued ${r?.requeued ?? 0} of ${r?.eligible ?? 0} failed documents`, {
            description: r?.remaining ? `${r.remaining} more are waiting for the next retry.` : undefined,
        });
        refresh();
    });
    const rescan = () => run("rescan", () => api.rescanConnector(c.id), () => {
        toast.success("Re-scan scheduled", { description: "The next sync re-reads the import window. Sources deleted on GitHub are not detected automatically." });
        refresh();
    });
    const rearm = () => run("rearm", () => api.rearmQuarantined(c.id), (r) => {
        toast.success(`Re-armed ${r?.rearmed ?? 0} quarantined item(s)`, {
            description: "The next sync re-reads their scopes and retries them.",
        });
        refresh();
    });
    const paused = (status?.status ?? c.status) === "paused";
    const togglePause = () => run("pause", () => api.updateConnector(c.id, { status: paused ? "active" : "paused" }), () => {
        refresh();
        onChanged?.();
    });

    const disconnected = (status?.status ?? c.status) === "disconnected";
    const docs = status?.documents ?? null;
    const scopes = Array.isArray(status?.scopes) ? status.scopes : [];
    const filtered = status?.filtered && typeof status.filtered === "object" ? Object.entries(status.filtered) : [];
    const run_ = status?.current_run ?? null;
    const lastErrorText = describeErrorCode(status?.last_error_code ?? c.last_error_code);
    const Icon = isGithub ? Github : FileText;
    const repos = Array.isArray(c.config?.repos) ? c.config.repos : [];
    const canAct = isGithub && !disconnected;
    const syncDisabled = !canAct || paused || syncing || watching || busy !== null;

    return (
        <div key={c.id} data-testid={`connector-${c.id}`} className="sm-card p-5">
            <div className="flex items-start justify-between mb-4">
                <div className="flex items-center gap-3 min-w-0">
                    <div className="w-10 h-10 rounded-lg bg-sm-bg border border-sm-border flex items-center justify-center shrink-0">
                        <Icon className="w-5 h-5" style={{ color: "#E8E8F0" }} />
                    </div>
                    <div className="min-w-0">
                        <div className="text-[14px] font-semibold text-sm-text truncate font-mono">{c.display_name}</div>
                        <div className="font-mono text-[10.5px] text-sm-text-secondary uppercase tracking-wider mt-0.5">
                            {c.connector_type}
                        </div>
                    </div>
                </div>
                {state ? (
                    <span data-testid={`connector-state-${c.id}`}>
                        <StatusBadge status={state} label={STATE_LABEL[state] || state} pulse={syncing} />
                    </span>
                ) : (
                    <StatusBadge status={c.status} pulse={c.status === "active"} />
                )}
            </div>

            {!isGithub && (
                <p data-testid={`connector-unsupported-${c.id}`} className="mb-3 text-[12px] text-sm-text-secondary">
                    This source type is not supported yet. It is listed for reference only and does not sync.
                </p>
            )}

            {repos.length > 0 && (
                <div className="flex items-center gap-1.5 flex-wrap mb-3" data-testid={`connector-repos-${c.id}`}>
                    {repos.map((r) => (
                        <span key={r} className="font-mono text-[10px] px-1.5 py-0.5 rounded border border-sm-border text-sm-text-secondary">{r}</span>
                    ))}
                </div>
            )}

            <div className="grid grid-cols-3 gap-4 mb-4 pb-4 border-b border-sm-border">
                <Fact label="Last synced" value={when(status?.last_sync_at ?? c.last_sync_at)} testId={`last-sync-${c.id}`} />
                <Fact label="Last success" value={when(status?.last_success_at ?? c.last_success_at)} testId={`last-success-${c.id}`} />
                <Fact label="Next sync" value={paused || disconnected ? "not scheduled" : whenFuture(status?.next_sync_at ?? c.next_sync_at)} testId={`next-sync-${c.id}`} />
            </div>

            {statusError && (
                <p data-testid={`status-stale-${c.id}`} className="mb-3 text-[11.5px] text-sm-amber">
                    {status ? "Could not refresh status; what is shown may be out of date." : "Status is unavailable right now."}
                </p>
            )}

            {run_ && (
                <div data-testid={`sync-progress-${c.id}`} role="status" className="mb-3 rounded-md border border-sm-blue/30 bg-sm-blue/10 p-3 text-[12px] text-sm-text">
                    <div className="font-medium mb-1">Sync in progress ({run_.trigger}, {run_.sync_type})</div>
                    <div className="font-mono text-[11px] text-sm-text-secondary">
                        found {formatCount(run_.artifacts_found)} · new {formatCount(run_.artifacts_new)} · updated {formatCount(run_.artifacts_updated)} · skipped {formatCount(run_.artifacts_skipped)} · failed {formatCount(run_.artifacts_failed)}
                    </div>
                </div>
            )}

            {lastErrorText && (
                <div data-testid={`connector-error-${c.id}`} className="mb-3 text-[11.5px] text-sm-red bg-sm-red/10 border border-sm-red/20 rounded-md p-2">
                    {lastErrorText}
                </div>
            )}

            {docs && (
                <div data-testid={`doc-counts-${c.id}`} className="grid grid-cols-3 gap-2 mb-3">
                    <Count label="Imported" value={docs.completed} />
                    <Count label="Pending" value={(docs.pending ?? 0) + (docs.processing ?? 0)} />
                    <Count label="Zero facts" value={docs.zero_facts} />
                    <Count label="Failed" value={docs.failed} warn={docs.failed > 0} testId={`count-failed-${c.id}`} />
                    <Count label="Quarantined" value={docs.quarantined} warn={docs.quarantined > 0} testId={`count-quarantined-${c.id}`} />
                </div>
            )}

            {filtered.length > 0 && (
                <p data-testid={`filtered-${c.id}`} className="mb-3 text-[11.5px] text-sm-text-secondary">
                    Left out on purpose:{" "}
                    {filtered.map(([reason, n], i) => (
                        <span key={reason}>{i > 0 ? ", " : ""}{formatCount(n)} {FILTER_LABEL[reason] || reason}</span>
                    ))}
                </p>
            )}

            {canAct && (
                <p data-testid={`deletion-note-${c.id}`} className="mb-3 text-[11.5px] text-sm-text-secondary">
                    Deleted sources are not detected automatically: memories from an issue, comment or
                    commit removed on GitHub stay until you remove them or disconnect with "remove memories".
                </p>
            )}

            {scopes.length > 0 && (
                <table data-testid={`scope-table-${c.id}`} className="w-full text-[11.5px] mb-3">
                    <thead>
                        <tr className="text-left font-mono text-[10px] uppercase tracking-wider text-sm-text-secondary border-b border-sm-border">
                            <th className="py-1.5 pr-2">Repository</th>
                            <th className="py-1.5 pr-2">Kind</th>
                            <th className="py-1.5">Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        {scopes.map((s) => (
                            <tr key={`${s.repo}:${s.artifact_type}`} className="border-b border-sm-border/50 align-top" data-testid={`scope-${s.repo}-${s.artifact_type}`}>
                                <td className="py-1.5 pr-2 font-mono">{s.repo}</td>
                                <td className="py-1.5 pr-2 font-mono text-sm-text-secondary">{ARTIFACT_LABEL[s.artifact_type] || s.artifact_type}</td>
                                <td className="py-1.5">
                                    <div className="font-medium" data-testid={`scope-status-${s.repo}-${s.artifact_type}`}>{SCOPE_STATUS_LABEL[s.status] || s.status}</div>
                                    <div className="text-sm-text-secondary">{SCOPE_STATUS_TEXT[s.status] || "Status not recognised."}</div>
                                    {s.last_error_code && s.status !== "ok" && (
                                        <div className="font-mono text-[10px] text-sm-text-muted">code: {s.last_error_code}</div>
                                    )}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            )}

            {actionError && (
                <p role="alert" data-testid={`action-error-${c.id}`} className="mb-3 text-[12px] text-sm-red">{actionError}</p>
            )}

            <div className="flex items-center gap-2 flex-wrap">
                {canAct && (
                    <Button
                        data-testid={`sync-now-${c.id}`}
                        onClick={syncNow}
                        disabled={syncDisabled}
                        size="sm"
                        className="flex-1 bg-white/[0.04] hover:bg-white/[0.08] border border-sm-border text-sm-text h-8 disabled:opacity-50"
                    >
                        <RefreshCw className={`w-3.5 h-3.5 ${syncing || busy === "sync" ? "animate-spin" : ""}`} />
                        {syncing ? "Syncing…" : busy === "sync" ? "Starting…" : "Sync now"}
                    </Button>
                )}
                {canAct && docs?.failed > 0 && (
                    <Button data-testid={`retry-failed-${c.id}`} onClick={retryFailed} disabled={busy !== null} size="sm" variant="outline" className="h-8 border-sm-border text-sm-text">
                        <RotateCw className="w-3.5 h-3.5" /> Retry failed
                    </Button>
                )}
                {canAct && docs?.quarantined > 0 && (
                    <Button data-testid={`rearm-${c.id}`} onClick={rearm} disabled={busy !== null} size="sm" variant="outline" className="h-8 border-sm-border text-sm-text">
                        <RotateCw className="w-3.5 h-3.5" /> Re-arm quarantined
                    </Button>
                )}
                {canAct && (
                    <Button data-testid={`rescan-${c.id}`} onClick={rescan} disabled={busy !== null} size="sm" variant="outline" className="h-8 border-sm-border text-sm-text">
                        <ScanSearch className="w-3.5 h-3.5" /> Re-scan
                    </Button>
                )}
                {canAct && (
                    <Button data-testid={`pause-${c.id}`} onClick={togglePause} disabled={busy !== null} size="sm" variant="outline" className="h-8 border-sm-border text-sm-text">
                        {paused ? <><Play className="w-3.5 h-3.5" /> Resume</> : <><Pause className="w-3.5 h-3.5" /> Pause</>}
                    </Button>
                )}
                <Button
                    onClick={() => onLogs(c)}
                    size="sm"
                    className="bg-white/[0.04] hover:bg-white/[0.08] border border-sm-border text-sm-text-secondary h-8"
                    data-testid={`view-logs-${c.id}`}
                >
                    View logs
                </Button>
                {!disconnected && (
                    <Button data-testid={`disconnect-${c.id}`} onClick={() => onDisconnect(c)} size="sm" variant="ghost" className="h-8 text-sm-red hover:text-sm-red">
                        <Unplug className="w-3.5 h-3.5" /> Disconnect
                    </Button>
                )}
            </div>
        </div>
    );
}

function Fact({ label, value, testId }) {
    return (
        <div>
            <div className="font-mono text-[10px] text-sm-text-secondary uppercase mb-1">{label}</div>
            <div data-testid={testId} className="font-mono text-[12.5px] text-sm-text">{value}</div>
        </div>
    );
}

function Count({ label, value, warn, testId }) {
    return (
        <div className="rounded-md border border-sm-border bg-sm-bg/40 px-2 py-1.5">
            <div className="font-mono text-[9.5px] uppercase text-sm-text-secondary">{label}</div>
            <div data-testid={testId} className={`font-mono text-[13px] ${warn ? "text-sm-amber" : "text-sm-text"}`}>{formatCount(value)}</div>
        </div>
    );
}
