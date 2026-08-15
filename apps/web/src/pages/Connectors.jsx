import React, { useEffect, useState } from "react";
import { Plus, RefreshCw, Settings2, Github, MessageSquare, Upload, FileText } from "lucide-react";
import TopBar from "../components/layout/TopBar";
import StatusBadge from "../components/widgets/StatusBadge";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "../components/ui/sheet";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Checkbox } from "../components/ui/checkbox";
import { toast } from "sonner";
import api from "../lib/api";
import { relativeTime } from "../lib/format";

const SOURCE_ICON = {
    github: Github,
    discord: MessageSquare,
};

export default function Connectors() {
    const [list, setList] = useState([]);
    const [open, setOpen] = useState(false);
    const [logsOpen, setLogsOpen] = useState(false);
    const [selectedConn, setSelectedConn] = useState(null);

    const load = () => api.listConnectors("ws").then(r => setList(r.connectors));
    useEffect(() => { load(); }, []);

    const syncNow = async (id) => {
        toast.info("Sync triggered");
        await api.triggerSync(id);
        toast.success("Sync started", { description: "Check logs for progress." });
    };

    return (
        <>
            <TopBar
                title="Connectors"
                subtitle={`${list.length} connected sources · ${list.reduce((a, c) => a + c.total_artifacts_synced, 0).toLocaleString()} artifacts synced`}
                actions={
                    <Button
                        data-testid="add-connector-btn"
                        onClick={() => setOpen(true)}
                        className="h-9 bg-sm-blue hover:bg-sm-blue/90 text-white"
                    >
                        <Plus className="w-4 h-4" /> Add Connector
                    </Button>
                }
            />
            <div className="flex-1 px-8 py-6">
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    {list.map((c) => {
                        const Icon = SOURCE_ICON[c.source_tool] || FileText;
                        const iconColor = c.source_tool === "github" ? "#E8E8F0" : "#5865F2";
                        return (
                            <div key={c.id} data-testid={`connector-${c.id}`} className="sm-card p-5">
                                <div className="flex items-start justify-between mb-4">
                                    <div className="flex items-center gap-3 min-w-0">
                                        <div className="w-10 h-10 rounded-lg bg-sm-bg border border-sm-border flex items-center justify-center shrink-0">
                                            <Icon className="w-5 h-5" style={{ color: iconColor }} />
                                        </div>
                                        <div className="min-w-0">
                                            <div className="text-[14px] font-semibold text-sm-text truncate font-mono">{c.name}</div>
                                            <div className="font-mono text-[10.5px] text-sm-text-secondary uppercase tracking-wider mt-0.5">
                                                {c.source_tool}
                                            </div>
                                        </div>
                                    </div>
                                    <StatusBadge status={c.status} pulse={c.status === "active" || c.status === "syncing"} />
                                </div>

                                <div className="grid grid-cols-2 gap-4 mb-4 pb-4 border-b border-sm-border">
                                    <div>
                                        <div className="font-mono text-[10px] text-sm-text-secondary uppercase mb-1">Last synced</div>
                                        <div className="font-mono text-[12.5px] text-sm-text">{relativeTime(c.last_synced_at)}</div>
                                    </div>
                                    <div>
                                        <div className="font-mono text-[10px] text-sm-text-secondary uppercase mb-1">Artifacts synced</div>
                                        <div className="font-mono text-[12.5px] text-sm-text">{c.total_artifacts_synced.toLocaleString()}</div>
                                    </div>
                                </div>

                                {c.status === "error" && c.error && (
                                    <div className="mb-3 text-[11.5px] text-sm-red font-mono bg-sm-red/10 border border-sm-red/20 rounded-md p-2">
                                        ⚠ {c.error}
                                    </div>
                                )}

                                {c.config?.artifact_types && (
                                    <div className="flex items-center gap-1.5 flex-wrap mb-4">
                                        {c.config.artifact_types.map(t => (
                                            <span key={t} className="font-mono text-[10px] px-1.5 py-0.5 rounded border border-sm-border text-sm-text-secondary">{t}</span>
                                        ))}
                                        {c.config.frequency && (
                                            <span className="font-mono text-[10px] px-1.5 py-0.5 rounded border border-sm-blue/30 bg-sm-blue/10 text-sm-blue ml-auto">
                                                {c.config.frequency}
                                            </span>
                                        )}
                                    </div>
                                )}

                                <div className="flex items-center gap-2">
                                    <Button
                                        data-testid={`sync-now-${c.id}`}
                                        onClick={() => syncNow(c.id)}
                                        size="sm"
                                        className="flex-1 bg-white/[0.04] hover:bg-white/[0.08] border border-sm-border text-sm-text h-8"
                                    >
                                        <RefreshCw className="w-3.5 h-3.5" /> Sync Now
                                    </Button>
                                    <Button
                                        onClick={() => { setSelectedConn(c); setLogsOpen(true); }}
                                        size="sm"
                                        className="bg-white/[0.04] hover:bg-white/[0.08] border border-sm-border text-sm-text-secondary h-8"
                                        data-testid={`view-logs-${c.id}`}
                                    >
                                        View Logs
                                    </Button>
                                    <Button size="sm" variant="ghost" className="h-8 w-8 p-0 text-sm-text-secondary hover:text-sm-text">
                                        <Settings2 className="w-3.5 h-3.5" />
                                    </Button>
                                </div>
                            </div>
                        );
                    })}
                </div>
            </div>

            <AddConnectorPanel open={open} onOpenChange={setOpen} onAdded={() => { setOpen(false); load(); toast.success("Connector added"); }} />
            <SyncLogsDrawer open={logsOpen} onOpenChange={setLogsOpen} connector={selectedConn} />
        </>
    );
}

function AddConnectorPanel({ open, onOpenChange, onAdded }) {
    const [step, setStep] = useState(1);
    const [type, setType] = useState(null);
    const [repo, setRepo] = useState("");
    const [types, setTypes] = useState({ commits: true, pulls: true, issues: true, discussions: false });
    const [freq, setFreq] = useState("hourly");
    const [channel, setChannel] = useState("");

    const reset = () => { setStep(1); setType(null); setRepo(""); setTypes({ commits: true, pulls: true, issues: true, discussions: false }); setFreq("hourly"); setChannel(""); };

    return (
        <Sheet open={open} onOpenChange={(v) => { onOpenChange(v); if (!v) reset(); }}>
            <SheetContent side="right" className="sm:max-w-[520px] w-[520px] bg-sm-surface border-l border-sm-border text-sm-text p-0 overflow-y-auto" data-testid="add-connector-panel">
                <SheetHeader className="p-6 border-b border-sm-border">
                    <SheetTitle className="text-sm-text text-[17px] font-semibold">Add Connector</SheetTitle>
                    <div className="flex items-center gap-1.5 mt-2 font-mono text-[11px]">
                        <span className={step >= 1 ? "text-sm-blue" : "text-sm-text-muted"}>1. CHOOSE</span>
                        <span className="text-sm-text-muted">→</span>
                        <span className={step >= 2 ? "text-sm-blue" : "text-sm-text-muted"}>2. CONFIGURE</span>
                    </div>
                </SheetHeader>

                {step === 1 && (
                    <div className="p-6 grid grid-cols-2 gap-3">
                        <TypeCard icon={Github}         name="GitHub"  desc="Commits, PRs, issues, discussions" color="#E8E8F0" onClick={() => { setType("github"); setStep(2); }} testId="type-github" />
                        <TypeCard icon={MessageSquare}  name="Discord" desc="Channel JSON exports"              color="#5865F2" onClick={() => { setType("discord"); setStep(2); }} testId="type-discord" />
                        <TypeCard disabled name="Slack"  desc="Coming soon" icon={MessageSquare} color="#8888A8" />
                        <TypeCard disabled name="Notion" desc="Coming soon" icon={FileText}      color="#8888A8" />
                    </div>
                )}

                {step === 2 && type === "github" && (
                    <div className="p-6 space-y-5">
                        <div>
                            <label className="block text-[12px] text-sm-text-secondary mb-2 font-medium">Repository URL</label>
                            <Input data-testid="github-repo" value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="github.com/acme/platform" className="bg-sm-bg/60 border-sm-border font-mono text-[12.5px]" />
                        </div>
                        <div>
                            <label className="block text-[12px] text-sm-text-secondary mb-2 font-medium">Artifact types</label>
                            <div className="space-y-2">
                                {Object.keys(types).map((k) => (
                                    <label key={k} className="flex items-center gap-2.5 h-9 px-3 rounded-md border border-sm-border bg-sm-bg/40 cursor-pointer">
                                        <Checkbox checked={types[k]} onCheckedChange={(v) => setTypes({ ...types, [k]: !!v })} className="border-sm-border data-[state=checked]:bg-sm-blue data-[state=checked]:border-sm-blue" />
                                        <span className="font-mono text-[12px] text-sm-text">{k}</span>
                                    </label>
                                ))}
                            </div>
                        </div>
                        <div>
                            <label className="block text-[12px] text-sm-text-secondary mb-2 font-medium">Sync frequency</label>
                            <Select value={freq} onValueChange={setFreq}>
                                <SelectTrigger className="bg-sm-bg/60 border-sm-border text-sm-text"><SelectValue /></SelectTrigger>
                                <SelectContent className="bg-sm-surface border-sm-border text-sm-text">
                                    <SelectItem value="realtime" className="focus:bg-white/5 focus:text-sm-text">Real-time (webhook)</SelectItem>
                                    <SelectItem value="hourly"   className="focus:bg-white/5 focus:text-sm-text">Hourly</SelectItem>
                                    <SelectItem value="daily"    className="focus:bg-white/5 focus:text-sm-text">Daily</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="flex items-center gap-2 pt-2">
                            <Button onClick={() => setStep(1)} variant="outline" className="bg-white/[0.03] border-sm-border text-sm-text hover:bg-white/[0.06]">Back</Button>
                            <Button onClick={onAdded} data-testid="connector-connect-btn" className="flex-1 bg-sm-blue hover:bg-sm-blue/90 text-white">Connect</Button>
                        </div>
                    </div>
                )}

                {step === 2 && type === "discord" && (
                    <div className="p-6 space-y-5">
                        <div>
                            <label className="block text-[12px] text-sm-text-secondary mb-2 font-medium">JSON export file</label>
                            <div className="rounded-lg border border-dashed border-sm-border bg-sm-bg/40 p-8 text-center hover:border-sm-blue/40 transition-colors cursor-pointer">
                                <Upload className="w-6 h-6 text-sm-text-secondary mx-auto mb-2" />
                                <p className="text-[12.5px] text-sm-text">Drop DiscordChatExporter .json here</p>
                                <p className="font-mono text-[11px] text-sm-text-muted mt-1">or click to browse</p>
                            </div>
                        </div>
                        <div>
                            <label className="block text-[12px] text-sm-text-secondary mb-2 font-medium">Channel name</label>
                            <Input value={channel} onChange={(e) => setChannel(e.target.value)} placeholder="#architecture" className="bg-sm-bg/60 border-sm-border font-mono text-[12.5px]" />
                        </div>
                        <div className="flex items-center gap-2 pt-2">
                            <Button onClick={() => setStep(1)} variant="outline" className="bg-white/[0.03] border-sm-border text-sm-text hover:bg-white/[0.06]">Back</Button>
                            <Button onClick={onAdded} className="flex-1 bg-sm-blue hover:bg-sm-blue/90 text-white">Import</Button>
                        </div>
                    </div>
                )}
            </SheetContent>
        </Sheet>
    );
}

function TypeCard({ icon: Icon, name, desc, color, onClick, disabled, testId }) {
    return (
        <button
            data-testid={testId}
            onClick={onClick}
            disabled={disabled}
            className={`rounded-lg border p-5 text-left transition-all ${disabled ? "border-sm-border bg-sm-surface/40 opacity-50 cursor-not-allowed" : "border-sm-border bg-sm-bg/40 hover:border-sm-blue/50 hover:bg-sm-blue/5"}`}
        >
            <div className="w-10 h-10 rounded-lg bg-sm-surface border border-sm-border flex items-center justify-center mb-3">
                <Icon className="w-5 h-5" style={{ color }} />
            </div>
            <div className="text-[14px] font-semibold text-sm-text mb-1">{name}</div>
            <div className="text-[11.5px] text-sm-text-secondary">{desc}</div>
        </button>
    );
}

function SyncLogsDrawer({ open, onOpenChange, connector }) {
    const [logs, setLogs] = useState([]);
    useEffect(() => {
        if (open && connector) api.getSyncLogs(connector.id).then(r => setLogs(r.logs));
    }, [open, connector]);
    return (
        <Sheet open={open} onOpenChange={onOpenChange}>
            <SheetContent side="right" className="sm:max-w-[600px] w-[600px] bg-sm-surface border-l border-sm-border text-sm-text p-0 overflow-y-auto" data-testid="sync-logs-drawer">
                <SheetHeader className="p-6 border-b border-sm-border">
                    <SheetTitle className="text-sm-text text-[17px] font-semibold">Sync Logs</SheetTitle>
                    <div className="font-mono text-[11.5px] text-sm-text-secondary">{connector?.name}</div>
                </SheetHeader>
                <div className="p-6">
                    <table className="w-full text-[12px]">
                        <thead>
                            <tr className="text-left font-mono text-[10.5px] uppercase tracking-wider text-sm-text-secondary border-b border-sm-border">
                                <th className="py-2.5">Date</th>
                                <th className="py-2.5">Status</th>
                                <th className="py-2.5 text-right">Items</th>
                                <th className="py-2.5 text-right">Duration</th>
                            </tr>
                        </thead>
                        <tbody>
                            {logs.map((l, i) => (
                                <tr key={i} className="border-b border-sm-border/50">
                                    <td className="py-3 font-mono text-[11px] text-sm-text">{new Date(l.date).toLocaleString()}</td>
                                    <td className="py-3"><StatusBadge status={l.status} /></td>
                                    <td className="py-3 text-right font-mono">{l.items_synced}</td>
                                    <td className="py-3 text-right font-mono text-sm-text-secondary">{l.duration_ms}ms</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    {logs.some(l => l.error) && (
                        <div className="mt-4 space-y-2">
                            {logs.filter(l => l.error).map((l, i) => (
                                <div key={i} className="text-[11.5px] text-sm-red font-mono bg-sm-red/10 border border-sm-red/20 rounded-md p-2">
                                    ⚠ {l.error}
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </SheetContent>
        </Sheet>
    );
}
