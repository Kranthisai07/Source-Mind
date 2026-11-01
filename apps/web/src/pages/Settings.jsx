import React, { useState } from "react";
import { Link2, Trash2, UserPlus } from "lucide-react";
import { toast } from "sonner";
import TopBar from "../components/layout/TopBar";
import ContributorAvatar from "../components/widgets/ContributorAvatar";
import { PageLoadError, PageLoading } from "../components/widgets/PageLoadState";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../components/ui/tabs";
import { Input } from "../components/ui/input";
import { Button } from "../components/ui/button";
import useApiResource from "../hooks/useApiResource";
import api from "../lib/api";
import { formatDate } from "../lib/format";
// Owner/admin is the same capability check the pilot tools use.
import { isPilotOperatorRole } from "../lib/pilotMode";
import AddMemberDialog from "../components/workspace/AddMemberDialog";
import McpKeysSection from "../components/settings/McpKeysSection";
import InviteDialog from "../components/team/InviteDialog";
import PendingInvitations from "../components/team/PendingInvitations";
import ActivityTab from "../components/team/ActivityTab";
import { assignableRoles, roleLabel, ROLE_SUMMARIES } from "../lib/roles";
import { mapWorkspaceError } from "../lib/workspaceErrors";

const ACCESS_BY_ROLE = {
    owner: "Full",
    admin: "Manage",
    member: "Add & read",
    viewer: "Read-only",
};

export default function Settings() {
    const workspaceResource = useApiResource(() => api.getWorkspace());
    const membersResource = useApiResource(() => api.listTeamMembers());
    const workspace = workspaceResource.data;
    const members = Array.isArray(membersResource.data?.members) ? membersResource.data.members : [];
    const workspaceName = workspace?.name ?? "";
    const workspaceSlug = workspace?.slug ?? "";

    // Same rule as PilotTools: the caller's role in THIS workspace comes from
    // the members list, matched on the signed-in user's id.
    const meResource = useApiResource(() => api.getCurrentUser());
    const me = meResource.data;
    const myMembership = me
        ? members.find((item) => (item.user || item).id === me.id)
        : null;
    const canAdd = Boolean(workspace?.id) && isPilotOperatorRole(myMembership?.role);
    const permissionsKnown = !meResource.loading && !membersResource.loading;
    const addMemberHint = !permissionsKnown
        ? "Checking your permissions..."
        : "Only workspace owners and admins can add members.";
    const [addOpen, setAddOpen] = useState(false);
    const [inviteOpen, setInviteOpen] = useState(false);
    const [invitesKey, setInvitesKey] = useState(0);
    const [roleBusy, setRoleBusy] = useState(null);
    const [roleError, setRoleError] = useState(null);
    const myRole = myMembership?.role;

    const changeRole = async (member, role) => {
        if (roleBusy) return;
        setRoleBusy(member.id);
        setRoleError(null);
        try {
            await api.changeMemberRole(workspace.id, member.id, role);
            toast.success(`${member.display_name || member.name || "Member"} is now ${roleLabel(role)}`);
            membersResource.retry();
        } catch (err) {
            setRoleError(mapWorkspaceError(err, "changeRole").message);
            membersResource.retry();
        } finally {
            setRoleBusy(null);
        }
    };

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
                        {canAdd && <TabsTrigger value="activity" data-testid="tab-activity" className="data-[state=active]:bg-sm-blue/15 data-[state=active]:text-sm-blue text-sm-text-secondary">Activity</TabsTrigger>}
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
                                <span title={canAdd ? undefined : addMemberHint} className="flex gap-2">
                                    <Button
                                        type="button"
                                        variant="outline"
                                        disabled={!canAdd}
                                        onClick={() => setInviteOpen(true)}
                                        data-testid="invite-btn"
                                        className="h-9 border-sm-border text-sm-text disabled:opacity-40 disabled:cursor-not-allowed"
                                    >
                                        <Link2 className="w-4 h-4" /> Invite by link
                                    </Button>
                                    <Button
                                        type="button"
                                        disabled={!canAdd}
                                        onClick={() => setAddOpen(true)}
                                        data-testid="add-member-btn"
                                        aria-describedby={canAdd ? undefined : "add-member-hint"}
                                        className="bg-sm-blue text-white h-9 disabled:opacity-40 disabled:cursor-not-allowed"
                                    >
                                        <UserPlus className="w-4 h-4" /> Add member
                                    </Button>
                                </span>
                            </div>
                            {!canAdd && (
                                <p id="add-member-hint" data-testid="add-member-hint" className="-mt-3 mb-4 text-[11.5px] text-sm-text-muted text-right">
                                    {addMemberHint}
                                </p>
                            )}
                            <AddMemberDialog
                                open={addOpen}
                                onOpenChange={setAddOpen}
                                workspaceId={workspace?.id}
                                workspaceName={workspace?.name}
                                onAdded={membersResource.retry}
                            />
                            <InviteDialog
                                open={inviteOpen}
                                onOpenChange={setInviteOpen}
                                workspaceId={workspace?.id}
                                callerRole={myRole}
                                onInvited={() => setInvitesKey((k) => k + 1)}
                            />
                            {roleError && <p role="alert" data-testid="role-error" className="mb-3 text-[12.5px] text-sm-red">{roleError}</p>}
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
                                                <RoleCell
                                                    member={member}
                                                    role={role}
                                                    options={canAdd && member.id !== me?.id ? assignableRoles(myRole, role) : []}
                                                    busy={roleBusy === member.id}
                                                    onChange={(next) => changeRole(member, next)}
                                                />
                                                <span className="text-[12px] text-sm-text-secondary">{ACCESS_BY_ROLE[role] || "—"}</span>
                                                <span className="font-mono text-[10.5px] text-sm-text-secondary text-right">{membership.joined_at ? formatDate(membership.joined_at) : "—"}</span>
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </section>
                        {canAdd && <PendingInvitations workspaceId={workspace?.id} refreshKey={invitesKey} />}
                    </TabsContent>

                    {canAdd && (
                        <TabsContent value="activity" className="mt-0">
                            <ActivityTab workspaceId={workspace.id} />
                        </TabsContent>
                    )}

                    <TabsContent value="api" className="mt-0">
                        <McpKeysSection />
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

function RoleCell({ member, role, options, busy, onChange }) {
    if (options.length === 0) {
        return <span title={ROLE_SUMMARIES[role]} className="font-mono text-[10.5px] uppercase text-sm-text">{roleLabel(role)}</span>;
    }
    return (
        <select
            aria-label={`Role for ${member.display_name || member.name || member.id}`}
            data-testid={`role-select-${member.id}`}
            value=""
            disabled={busy}
            onChange={(e) => e.target.value && onChange(e.target.value)}
            className="h-8 rounded-md bg-sm-bg/60 border border-sm-border text-sm-text text-[11px] px-1"
        >
            <option value="">{roleLabel(role)} ▾</option>
            {options.map((r) => <option key={r} value={r}>Make {roleLabel(r)}</option>)}
        </select>
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
