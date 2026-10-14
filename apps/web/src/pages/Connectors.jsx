import React, { useCallback, useEffect, useRef, useState } from "react";
import { Plus, RefreshCw } from "lucide-react";
import TopBar from "../components/layout/TopBar";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "../components/ui/sheet";
import { Button } from "../components/ui/button";
import { toast } from "sonner";
import api from "../lib/api";
import { classifyApiError } from "../lib/apiError";
import ConnectorCard from "../components/connectors/ConnectorCard";
import AddConnectorFlow from "../components/connectors/AddConnectorFlow";
import SyncLogsDrawer from "../components/connectors/SyncLogsDrawer";
import DisconnectDialog from "../components/connectors/DisconnectDialog";
import { takeGithubCallback } from "../components/connectors/connectorText";

export default function Connectors() {
    const [list, setList] = useState([]);
    const [open, setOpen] = useState(false);
    const [callback, setCallback] = useState(null);
    const [logsOpen, setLogsOpen] = useState(false);
    const [selectedConn, setSelectedConn] = useState(null);
    const [disconnecting, setDisconnecting] = useState(null);
    const [error, setError] = useState(null);
    const generation = useRef(0);
    const alive = useRef(true);
    const callbackTaken = useRef(false);

    useEffect(() => {
        alive.current = true;
        return () => { alive.current = false; };
    }, []);

    // Returning from GitHub: the callback route parked code+state in memory.
    // Take it once (a ref keeps a Strict Mode re-run from taking nothing and
    // closing the panel) and open the panel in "finishing" mode.
    useEffect(() => {
        if (callbackTaken.current) return;
        callbackTaken.current = true;
        const pending = takeGithubCallback();
        if (pending) {
            setCallback(pending);
            setOpen(true);
        }
    }, []);

    const load = useCallback(() => {
        const requestGeneration = ++generation.current;
        const isCurrent = () => alive.current && requestGeneration === generation.current;

        return api.listConnectors().then(
            (response) => {
                if (!isCurrent()) return;
                setList(response.connectors);
                setError(null);
            },
            (requestError) => {
                if (!isCurrent()) return;
                setError(classifyApiError(requestError));
            }
        );
    }, []);

    useEffect(() => { load(); }, [load]);

    const closePanel = () => { setOpen(false); setCallback(null); };

    return (
        <>
            <TopBar
                title="Connectors"
                subtitle={error ? "Connector list unavailable" : `${list.length} connected sources`}
                actions={
                    <Button
                        data-testid="add-connector-btn"
                        onClick={() => setOpen(true)}
                        className="h-9 bg-sm-blue hover:bg-sm-blue/90 text-white"
                    >
                        <Plus className="w-4 h-4" /> Add connector
                    </Button>
                }
            />
            <div className="flex-1 px-8 py-6">
                {error && (
                    <div data-testid="connectors-error" className="sm-card p-6 mb-4 border-sm-red/30">
                        <div className="text-[14px] font-semibold text-sm-text">{error.title}</div>
                        <p className="mt-1 text-[12.5px] text-sm-text-secondary">{error.detail}</p>
                        {error.retryable && (
                            <Button
                                data-testid="error-retry"
                                onClick={load}
                                size="sm"
                                className="mt-4 bg-sm-blue hover:bg-sm-blue/90 text-white"
                            >
                                <RefreshCw className="w-3.5 h-3.5" /> Retry
                            </Button>
                        )}
                    </div>
                )}
                {!error && list.length === 0 && (
                    <p data-testid="connectors-empty" className="text-[13px] text-sm-text-secondary">
                        No sources are connected yet. Use Add connector to import from GitHub.
                    </p>
                )}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    {!error && list.map((c) => (
                        <ConnectorCard
                            key={c.id}
                            connector={c}
                            onLogs={(conn) => { setSelectedConn(conn); setLogsOpen(true); }}
                            onDisconnect={setDisconnecting}
                            onChanged={load}
                        />
                    ))}
                </div>
            </div>

            <Sheet open={open} onOpenChange={(v) => { if (!v) closePanel(); else setOpen(true); }}>
                <SheetContent side="right" className="sm:max-w-[520px] w-[520px] bg-sm-surface border-l border-sm-border text-sm-text p-0 overflow-y-auto" data-testid="add-connector-panel">
                    <SheetHeader className="p-6 border-b border-sm-border">
                        <SheetTitle className="text-sm-text text-[17px] font-semibold">Add connector</SheetTitle>
                        <SheetDescription className="sr-only">Connect GitHub and choose repositories to import.</SheetDescription>
                    </SheetHeader>
                    {open && (
                        <AddConnectorFlow
                            callback={callback}
                            onClose={closePanel}
                            onCreated={(created) => {
                                closePanel();
                                load();
                                toast.success("Connector created", {
                                    description: `${created?.display_name ?? "GitHub"} will start importing on its first sync.`,
                                });
                            }}
                        />
                    )}
                </SheetContent>
            </Sheet>
            <SyncLogsDrawer open={logsOpen} onOpenChange={setLogsOpen} connector={selectedConn} />
            {disconnecting && (
                <DisconnectDialog
                    connector={disconnecting}
                    onClose={() => setDisconnecting(null)}
                    onDone={(result) => {
                        const name = disconnecting.display_name;
                        setDisconnecting(null);
                        load();
                        toast.success(`${name} disconnected`, {
                            description: result?.remove_memories
                                ? `${result.memories_retired ?? 0} memories removed, ${result.memories_flagged ?? 0} kept and marked as source removed.`
                                : "Imported memories were kept.",
                        });
                    }}
                />
            )}
        </>
    );
}
