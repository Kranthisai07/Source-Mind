import React from "react";
import { KeyRound, Trash2, UserPlus } from "lucide-react";
import TopBar from "../components/layout/TopBar";
import ContributorAvatar from "../components/widgets/ContributorAvatar";
import { PageLoadError, PageLoading } from "../components/widgets/PageLoadState";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../components/ui/tabs";
import { Input } from "../components/ui/input";
import { Button } from "../components/ui/button";
import useApiResource from "../hooks/useApiResource";
import api from "../lib/api";
import { formatDate } from "../lib/format";

const ACCESS_BY_ROLE = {
    owner: "Full",
    admin: "Manage",
    member: "Edit",
    viewer: "Read-only",
};

export default function Settings() {
    const workspaceResource = useApiResource(() => api.getWorkspace());
    const membersResource = useApiResource(() => api.listTeamMembers());
    const workspace = workspaceResource.data;
    const members = Array.isArray(membersResource.data?.members) ? membersResource.data.members : [];
    const workspaceName = workspace?.name ?? "";
    const workspaceSlug = workspace?.slug ?? "";

    return (
        <>
            <TopBar
                title="Settings"
                subtitle={workspace ? `Workspace and access for ${workspace.name}` : "Workspace and access"}
            />
            <div className="flex-1 px-8 py-6">
                <Tabs defaultValue="workspace" className="w-full">
                    <TabsList className="bg-sm-surface border border-sm-border p-1 h-10 mb-6" data-testid="settings-tabs">
                        <TabsTrigger value="workspace" data-testid="tab-workspace" className="data-[state=active]:bg-sm-blue/15 data-[state=active]:text-sm-blue text-sm-text-secondary">Workspace</TabsTrigger>
                        <TabsTrigger value="team" data-testid="tab-team" className="data-[state=active]:bg-sm-blue/15 data-[state=active]:text-sm-blue text-sm-text-secondary">Team</TabsTrigger>
                        <TabsTrigger value="api" data-testid="tab-api" className="data-[state=active]:bg-sm-blue/15 data-[state=active]:text-sm-blue text-sm-text-secondary">API Keys</TabsTrigger>
                        <TabsTrigger value="danger" data-testid="tab-danger" className="data-[state=active]:bg-sm-red/15 data-[state=active]:text-sm-red text-sm-text-secondary">Danger Zone</TabsTrigger>
                    </TabsList>

                    <TabsContent value="workspace" className="mt-0">
                        {workspaceResource.error ? (
                            <PageLoadError error={workspaceResource.error} onRetry={workspaceResource.retry} testId="workspace-error" />
                        ) : workspaceResource.loading && !workspace ? (
                            <PageLoading label="Loading workspace" testId="workspace-loading" />
                        ) : (
                            <section className="sm-card p-6 max-w-2xl">
                                <h3 className="text-[15px] font-semibold text-sm-text mb-4">Workspace</h3>
                                <div className="space-y-4">
                                    <Field label="Workspace name" hint="Read-only on this screen">
                                        <Input data-testid="ws-name" value={workspaceName} readOnly className="bg-sm-bg/60 border-sm-border text-sm-text h-10" />
                                    </Field>
                                    <Field label="Workspace slug" hint="Used by the API to scope workspace data">
                                        <div className="h-10 px-3 rounded-md bg-sm-bg/40 border border-sm-border flex items-center">
                                            <span className="font-mono text-[12.5px] text-sm-text select-all">{workspaceSlug || "—"}</span>
                                        </div>
                                    </Field>
                                    {workspace?.plan && (
                                        <Field label="Plan">
                                            <span className="font-mono text-[11px] font-semibold px-2 py-1 rounded-md bg-sm-blue/15 border border-sm-blue/30 text-sm-blue">
                                                {String(workspace.plan).toUpperCase()}
                                            </span>
                                        </Field>
                                    )}
                                </div>
                                <div className="mt-6 pt-5 border-t border-sm-border">
                                    <Button disabled data-testid="save-workspace" title="Workspace editing isn't exposed by the API yet" className="bg-sm-blue text-white disabled:opacity-40 disabled:cursor-not-allowed">Save changes</Button>
                                </div>
                            </section>
                        )}
                    </TabsContent>

                    <TabsContent value="team" className="mt-0">
                        <section className="sm-card p-6">
                            <div className="flex items-center justify-between mb-5">
                                <div>
                                    <h3 className="text-[15px] font-semibold text-sm-text">Team Members</h3>
                                    <p className="text-[12.5px] text-sm-text-secondary mt-0.5">
                                        {membersResource.loading ? "Loading members…" : `${members.length} member${members.length === 1 ? "" : "s"} in this workspace`}
                                    </p>
                                </div>
                                <Button disabled data-testid="invite-btn" title="No invitation endpoint exists yet" className="bg-sm-blue text-white h-9 disabled:opacity-40 disabled:cursor-not-allowed">
                                    <UserPlus className="w-4 h-4" /> Invite Member
                                </Button>
                            </div>
                            {membersResource.error ? (
                                <PageLoadError error={membersResource.error} onRetry={membersResource.retry} testId="members-error" compact />
                            ) : membersResource.loading ? (
                                <PageLoading label="Loading members" testId="members-loading" />
                            ) : members.length === 0 ? (
                                <div data-testid="members-empty" className="py-12 text-center text-[12.5px] text-sm-text-secondary">No members are recorded for this workspace.</div>
                            ) : (
                                <div className="space-y-2">
                                    {members.map((membership) => {
                                        const member = membership.user || membership;
                                        const role = String(membership.role || "member").toLowerCase();
                                        const displayName = member.display_name || member.name || member.id;
                                        return (
                                            <div key={member.id} data-testid={`member-${member.id}`} className="grid grid-cols-[auto_minmax(0,1fr)_100px_100px_110px] items-center gap-3 p-3 rounded-lg border border-sm-border bg-sm-bg/40">
                                                {member.avatar_url ? <img src={member.avatar_url} alt="" className="w-9 h-9 rounded-full object-cover" /> : <ContributorAvatar contributor={{ name: displayName }} size={36} />}
                                                <div className="min-w-0">
                                                    <div className="text-[13px] text-sm-text font-medium truncate">{displayName}</div>
                                                    <div className="font-mono text-[11px] text-sm-text-secondary truncate">{member.id}</div>
                                                </div>
                                                <span className="font-mono text-[10.5px] uppercase text-sm-text">{role}</span>
                                                <span className="text-[12px] text-sm-text-secondary">{ACCESS_BY_ROLE[role] || "—"}</span>
                                                <span className="font-mono text-[10.5px] text-sm-text-secondary text-right">{membership.joined_at ? formatDate(membership.joined_at) : "—"}</span>
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </section>
                    </TabsContent>

                    <TabsContent value="api" className="mt-0">
                        <section className="sm-card p-8 max-w-2xl text-center" data-testid="api-keys-empty">
                            <KeyRound className="w-9 h-9 text-sm-text-muted mx-auto mb-3" />
                            <h3 className="text-[15px] font-semibold text-sm-text">API keys aren't available yet</h3>
                            <p className="mt-1 text-[12.5px] text-sm-text-secondary">
                                The backend exposes no API-key endpoints. Browser requests use a Clerk session token.
                            </p>
                        </section>
                    </TabsContent>

                    <TabsContent value="danger" className="mt-0">
                        <section data-testid="danger-zone" className="rounded-xl border border-sm-red/40 bg-sm-red/5 p-6 max-w-2xl">
                            <h3 className="text-[15px] font-semibold text-sm-red mb-2">Delete Workspace</h3>
                            <p className="text-[13px] text-sm-text-secondary mb-4">
                                Workspace deletion isn't exposed by the API yet. No deletion will be simulated.
                            </p>
                            <Button disabled data-testid="delete-workspace-btn" title="No workspace-deletion endpoint exists yet" className="bg-sm-red text-white disabled:opacity-40 disabled:cursor-not-allowed">
                                <Trash2 className="w-4 h-4" /> Delete Workspace
                            </Button>
                        </section>
                    </TabsContent>
                </Tabs>
            </div>
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
