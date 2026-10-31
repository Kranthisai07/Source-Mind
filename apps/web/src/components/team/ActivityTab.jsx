import React, { useCallback, useEffect, useState } from "react";
import { Download } from "lucide-react";
import { toast } from "sonner";
import { Button } from "../ui/button";
import { PageLoading } from "../widgets/PageLoadState";
import api from "../../lib/api";
import { describeAuditEvent } from "../../lib/auditEvents";
import { formatDate } from "../../lib/format";
import { mapWorkspaceError } from "../../lib/workspaceErrors";

const PAGE = 50;

function download(data, workspaceId) {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `sourcemind-export-${workspaceId}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}

/** Founders/admins: who changed the team, plus a JSON export of the workspace's data (D-067). */
export default function ActivityTab({ workspaceId }) {
    const [items, setItems] = useState([]);
    const [more, setMore] = useState(false);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [exporting, setExporting] = useState(false);
    const [exportError, setExportError] = useState(null);

    const load = useCallback(
        async (beforeId) => {
            setLoading(true);
            setError(null);
            try {
                const page = await api.listWorkspaceAudit(workspaceId, { beforeId, limit: PAGE });
                setItems((prev) => (beforeId ? [...prev, ...page.items] : page.items));
                setMore(page.items.length === PAGE);
            } catch (err) {
                setError(mapWorkspaceError(err, "audit").message);
            } finally {
                setLoading(false);
            }
        },
        [workspaceId]
    );

    useEffect(() => {
        load();
    }, [load]);

    const runExport = async () => {
        if (exporting) return;
        setExporting(true);
        setExportError(null);
        try {
            download(await api.exportWorkspace(workspaceId), workspaceId);
            toast.success("Export downloaded");
            load();
        } catch (err) {
            setExportError(mapWorkspaceError(err, "export").message);
        } finally {
            setExporting(false);
        }
    };

    return (
        <section className="sm-card p-6" data-testid="activity-tab">
            <div className="flex items-start justify-between gap-4 mb-4">
                <div>
                    <h3 className="text-[15px] font-semibold text-sm-text">Activity</h3>
                    <p className="text-[12.5px] text-sm-text-secondary mt-0.5">Changes to members, roles and invitations, and data exports. Newest first.</p>
                </div>
                <Button type="button" variant="outline" disabled={exporting} onClick={runExport} data-testid="export-btn" className="h-9 border-sm-border text-sm-text shrink-0">
                    <Download className="w-4 h-4" /> {exporting ? "Exporting..." : "Export data"}
                </Button>
            </div>
            {exportError && <p role="alert" data-testid="export-error" className="mb-3 text-[12.5px] text-sm-red">{exportError}</p>}
            {error && (
                <p role="alert" data-testid="activity-error" className="mb-3 text-[12.5px] text-sm-red">
                    {error}{" "}
                    <button type="button" className="underline" onClick={() => load(items.length ? items[items.length - 1].id : undefined)}>Retry</button>
                </p>
            )}
            {loading && items.length === 0 ? (
                <PageLoading label="Loading activity" testId="activity-loading" />
            ) : items.length === 0 && !error ? (
                <div data-testid="activity-empty" className="py-6 text-center text-[12.5px] text-sm-text-secondary">No activity yet.</div>
            ) : (
                <ul className="space-y-2">
                    {items.map((e) => (
                        <li key={e.id} data-testid={`activity-${e.id}`} className="flex items-baseline justify-between gap-3 p-3 rounded-lg border border-sm-border bg-sm-bg/40">
                            <span className="text-[13px] text-sm-text">{describeAuditEvent(e)}</span>
                            <span className="font-mono text-[10.5px] text-sm-text-secondary shrink-0">{formatDate(e.at)}</span>
                        </li>
                    ))}
                </ul>
            )}
            {more && (
                <Button type="button" variant="outline" disabled={loading} onClick={() => load(items[items.length - 1].id)} data-testid="activity-more" className="mt-3 h-8 border-sm-border text-sm-text">
                    {loading ? "Loading..." : "Load more"}
                </Button>
            )}
        </section>
    );
}
