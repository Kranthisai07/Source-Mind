import React, { useEffect, useState } from "react";
import { Copy, Eye, EyeOff, RotateCw, Trash2, UserPlus, Check } from "lucide-react";
import TopBar from "../components/layout/TopBar";
import ContributorAvatar from "../components/widgets/ContributorAvatar";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../components/ui/tabs";
import { Input } from "../components/ui/input";
import { Button } from "../components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "../components/ui/dialog";
import { toast } from "sonner";
import api from "../lib/api";
import { WORKSPACE } from "../lib/mockData";

const API_KEY = "sm_live_4f7eff_a78bfa_34d399_b22c_k9qe2m3p6n8x1";

export default function Settings() {
    const [members, setMembers] = useState([]);
    const [wsName, setWsName] = useState(WORKSPACE.name);
    const [reveal, setReveal] = useState(false);
    const [inviteOpen, setInviteOpen] = useState(false);
    const [rotateOpen, setRotateOpen] = useState(false);
    const [deleteOpen, setDeleteOpen] = useState(false);
    const [confirmText, setConfirmText] = useState("");

    useEffect(() => { api.listTeamMembers().then(r => setMembers(r.members)); }, []);

    return (
        <>
            <TopBar title="Settings" subtitle={`Manage ${WORKSPACE.name} · plan ${WORKSPACE.plan}`} />
            <div className="flex-1 px-8 py-6">
                <Tabs defaultValue="workspace" className="w-full">
                    <TabsList className="bg-sm-surface border border-sm-border p-1 h-10 mb-6" data-testid="settings-tabs">
                        <TabsTrigger value="workspace" data-testid="tab-workspace" className="data-[state=active]:bg-sm-blue/15 data-[state=active]:text-sm-blue text-sm-text-secondary">Workspace</TabsTrigger>
                        <TabsTrigger value="team"      data-testid="tab-team"      className="data-[state=active]:bg-sm-blue/15 data-[state=active]:text-sm-blue text-sm-text-secondary">Team</TabsTrigger>
                        <TabsTrigger value="api"       data-testid="tab-api"       className="data-[state=active]:bg-sm-blue/15 data-[state=active]:text-sm-blue text-sm-text-secondary">API Keys</TabsTrigger>
                        <TabsTrigger value="danger"    data-testid="tab-danger"    className="data-[state=active]:bg-sm-red/15 data-[state=active]:text-sm-red text-sm-text-secondary">Danger Zone</TabsTrigger>
                    </TabsList>

                    {/* WORKSPACE */}
                    <TabsContent value="workspace" className="mt-0">
                        <section className="sm-card p-6 max-w-2xl">
                            <h3 className="text-[15px] font-semibold text-sm-text mb-4">Workspace</h3>
                            <div className="space-y-4">
                                <Field label="Workspace name">
                                    <Input data-testid="ws-name" value={wsName} onChange={(e) => setWsName(e.target.value)} className="bg-sm-bg/60 border-sm-border text-sm-text h-10" />
                                </Field>
                                <Field label="Workspace slug" hint="Read-only · used in URLs and API calls">
                                    <div className="flex items-center gap-2 h-10 px-3 rounded-md bg-sm-bg/40 border border-sm-border">
                                        <span className="font-mono text-[12.5px] text-sm-text-secondary">sourcemind.dev/</span>
                                        <span className="font-mono text-[12.5px] text-sm-text">{WORKSPACE.slug}</span>
                                    </div>
                                </Field>
                                <Field label="Plan">
                                    <div className="flex items-center gap-2">
                                        <span className="font-mono text-[11px] font-semibold px-2 py-1 rounded-md bg-sm-blue/15 border border-sm-blue/30 text-sm-blue">
                                            {WORKSPACE.plan.toUpperCase()}
                                        </span>
                                        <span className="text-[12.5px] text-sm-text-secondary">1,000 req/min · unlimited workspaces</span>
                                    </div>
                                </Field>
                            </div>
                            <div className="mt-6 pt-5 border-t border-sm-border">
                                <Button data-testid="save-workspace" onClick={() => toast.success("Workspace saved")} className="bg-sm-blue hover:bg-sm-blue/90 text-white">Save changes</Button>
                            </div>
                        </section>
                    </TabsContent>

                    {/* TEAM */}
                    <TabsContent value="team" className="mt-0">
                        <section className="sm-card p-6">
                            <div className="flex items-center justify-between mb-5">
                                <div>
                                    <h3 className="text-[15px] font-semibold text-sm-text">Team Members</h3>
                                    <p className="text-[12.5px] text-sm-text-secondary mt-0.5">{members.length} members in this workspace</p>
                                </div>
                                <Button data-testid="invite-btn" onClick={() => setInviteOpen(true)} className="bg-sm-blue hover:bg-sm-blue/90 text-white h-9">
                                    <UserPlus className="w-4 h-4" /> Invite Member
                                </Button>
                            </div>
                            <div className="space-y-2">
                                {members.map((m) => (
                                    <div key={m.id} data-testid={`member-${m.login}`} className="flex items-center gap-3 p-3 rounded-lg border border-sm-border bg-sm-bg/40">
                                        <ContributorAvatar contributor={m} size={36} />
                                        <div className="flex-1 min-w-0">
                                            <div className="text-[13px] text-sm-text font-medium truncate">{m.name}</div>
                                            <div className="font-mono text-[11px] text-sm-text-secondary truncate">{m.email}</div>
                                        </div>
                                        <span
                                            className={`font-mono text-[10.5px] font-semibold px-2 py-0.5 rounded-md tracking-wider border ${
                                                m.role === "Owner"  ? "bg-sm-purple/15 text-sm-purple border-sm-purple/30" :
                                                m.role === "Admin"  ? "bg-sm-blue/15 text-sm-blue border-sm-blue/30" :
                                                                      "bg-white/[0.04] text-sm-text-secondary border-sm-border"
                                            }`}
                                        >
                                            {m.role.toUpperCase()}
                                        </span>
                                        {m.role !== "Owner" && (
                                            <button className="w-8 h-8 rounded-md text-sm-text-muted hover:text-sm-red hover:bg-sm-red/10 flex items-center justify-center">
                                                <Trash2 className="w-3.5 h-3.5" />
                                            </button>
                                        )}
                                    </div>
                                ))}
                            </div>
                        </section>
                    </TabsContent>

                    {/* API */}
                    <TabsContent value="api" className="mt-0">
                        <section className="sm-card p-6 max-w-2xl">
                            <h3 className="text-[15px] font-semibold text-sm-text mb-4">API Keys</h3>
                            <Field label="Your API Key" hint="Use for server-to-server integrations. Rotate if compromised.">
                                <div className="flex items-center gap-2">
                                    <div className="flex-1 h-10 px-3 rounded-md bg-sm-bg/40 border border-sm-border flex items-center font-mono text-[12px] text-sm-text">
                                        {reveal ? API_KEY : API_KEY.replace(/./g, "•").slice(0, API_KEY.length)}
                                    </div>
                                    <button
                                        data-testid="reveal-key"
                                        onClick={() => setReveal(v => !v)}
                                        className="h-10 w-10 rounded-md border border-sm-border bg-sm-bg/40 text-sm-text-secondary hover:text-sm-text flex items-center justify-center"
                                        title={reveal ? "Hide" : "Reveal"}
                                    >
                                        {reveal ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                    </button>
                                    <button
                                        data-testid="copy-key"
                                        onClick={async () => {
                                            try {
                                                await navigator.clipboard.writeText(API_KEY);
                                                toast.success("Copied to clipboard");
                                            } catch {
                                                toast.error("Clipboard not available", { description: "Select and copy the key manually." });
                                            }
                                        }}
                                        className="h-10 w-10 rounded-md border border-sm-border bg-sm-bg/40 text-sm-text-secondary hover:text-sm-text flex items-center justify-center"
                                    >
                                        <Copy className="w-4 h-4" />
                                    </button>
                                </div>
                            </Field>
                            <div className="mt-6 pt-5 border-t border-sm-border">
                                <Button
                                    data-testid="rotate-key"
                                    onClick={() => setRotateOpen(true)}
                                    className="bg-sm-amber/15 hover:bg-sm-amber/25 text-sm-amber border border-sm-amber/30"
                                >
                                    <RotateCw className="w-4 h-4" /> Rotate key
                                </Button>
                                <p className="text-[11.5px] text-sm-text-secondary mt-2">
                                    Rotating will invalidate the current key immediately. Update any active integrations.
                                </p>
                            </div>
                        </section>
                    </TabsContent>

                    {/* DANGER */}
                    <TabsContent value="danger" className="mt-0">
                        <section className="rounded-xl border border-sm-red/40 bg-sm-red/5 p-6 max-w-2xl">
                            <h3 className="text-[15px] font-semibold text-sm-red mb-2">Delete Workspace</h3>
                            <p className="text-[13px] text-sm-text-secondary mb-4">
                                Permanently delete <span className="font-mono text-sm-text">{WORKSPACE.slug}</span>, all memories, conflicts, and connectors.
                                This action cannot be undone.
                            </p>
                            <Button
                                data-testid="delete-workspace-btn"
                                onClick={() => setDeleteOpen(true)}
                                className="bg-sm-red hover:bg-sm-red/90 text-white"
                            >
                                <Trash2 className="w-4 h-4" /> Delete Workspace
                            </Button>
                        </section>
                    </TabsContent>
                </Tabs>
            </div>

            {/* Invite modal */}
            <Dialog open={inviteOpen} onOpenChange={setInviteOpen}>
                <DialogContent className="bg-sm-surface border border-sm-border text-sm-text" data-testid="invite-modal">
                    <DialogHeader>
                        <DialogTitle className="text-sm-text">Invite a team member</DialogTitle>
                        <DialogDescription className="text-sm-text-secondary">They'll get an email invite with a link to join this workspace.</DialogDescription>
                    </DialogHeader>
                    <div className="space-y-3 mt-2">
                        <Input placeholder="teammate@company.dev" className="bg-sm-bg/60 border-sm-border text-sm-text h-10" />
                        <Select defaultValue="Member">
                            <SelectTrigger className="bg-sm-bg/60 border-sm-border text-sm-text"><SelectValue /></SelectTrigger>
                            <SelectContent className="bg-sm-surface border-sm-border">
                                <SelectItem value="Admin"  className="focus:bg-white/5 focus:text-sm-text">Admin</SelectItem>
                                <SelectItem value="Member" className="focus:bg-white/5 focus:text-sm-text">Member</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                    <DialogFooter>
                        <Button onClick={() => setInviteOpen(false)} variant="outline" className="bg-white/[0.03] border-sm-border text-sm-text hover:bg-white/[0.06]">Cancel</Button>
                        <Button onClick={() => { setInviteOpen(false); toast.success("Invite sent"); }} className="bg-sm-blue hover:bg-sm-blue/90 text-white">
                            <Check className="w-4 h-4" /> Send invite
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Rotate modal */}
            <Dialog open={rotateOpen} onOpenChange={setRotateOpen}>
                <DialogContent className="bg-sm-surface border border-sm-border text-sm-text">
                    <DialogHeader>
                        <DialogTitle className="text-sm-amber">Rotate API key?</DialogTitle>
                        <DialogDescription className="text-sm-text-secondary">The current key will be invalidated immediately.</DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button onClick={() => setRotateOpen(false)} variant="outline" className="bg-white/[0.03] border-sm-border text-sm-text hover:bg-white/[0.06]">Cancel</Button>
                        <Button onClick={() => { setRotateOpen(false); toast.success("Key rotated"); }} className="bg-sm-amber hover:bg-sm-amber/90 text-white">Rotate now</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Delete modal */}
            <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
                <DialogContent className="bg-sm-surface border border-sm-red/40 text-sm-text">
                    <DialogHeader>
                        <DialogTitle className="text-sm-red">Delete workspace?</DialogTitle>
                        <DialogDescription className="text-sm-text-secondary">
                            Type <span className="font-mono text-sm-text">{WORKSPACE.slug}</span> to confirm.
                        </DialogDescription>
                    </DialogHeader>
                    <Input
                        data-testid="delete-confirm"
                        value={confirmText}
                        onChange={(e) => setConfirmText(e.target.value)}
                        className="bg-sm-bg/60 border-sm-border text-sm-text font-mono h-10"
                    />
                    <DialogFooter>
                        <Button onClick={() => setDeleteOpen(false)} variant="outline" className="bg-white/[0.03] border-sm-border text-sm-text hover:bg-white/[0.06]">Cancel</Button>
                        <Button
                            disabled={confirmText !== WORKSPACE.slug}
                            onClick={() => { setDeleteOpen(false); toast.error("Workspace deleted (demo)", { description: "In production, this would queue a 30-day soft delete." }); setConfirmText(""); }}
                            className="bg-sm-red hover:bg-sm-red/90 text-white disabled:opacity-40"
                        >
                            Delete permanently
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </>
    );
}

function Field({ label, hint, children }) {
    return (
        <div>
            <label className="block text-[12px] text-sm-text-secondary mb-2 font-medium">{label}</label>
            {children}
            {hint && <p className="text-[11.5px] text-sm-text-muted mt-1.5">{hint}</p>}
        </div>
    );
}
